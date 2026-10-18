"""WGPU panorama sampling with OpenCV's 1/32-pixel interpolation contract."""
from __future__ import annotations

import cv2
import numpy as np


class GPUUnavailable(RuntimeError):
    """A GPU operation failed; the caller may retry another backend or CPU."""


SHADER = r"""
struct Size { sw: u32, sh: u32, ow: u32, oh: u32, offset: u32, p0: u32, p1: u32, p2: u32 }
@group(0) @binding(0) var source: texture_2d<u32>;
@group(0) @binding(1) var<storage, read> maps: array<vec2<u32>>;
@group(0) @binding(2) var<storage, read_write> result: array<u32>;
@group(0) @binding(3) var<uniform> size: Size;
fn pixel(x: i32, y: i32) -> vec4<u32> {
    let sw = i32(size.sw); let sh = i32(size.sh);
    return textureLoad(source, vec2<i32>((x % sw + sw) % sw, (y % sh + sh) % sh), 0);
}
@compute @workgroup_size(16, 16)
fn main(@builtin(global_invocation_id) id: vec3<u32>) {
    if (id.x >= size.ow || id.y >= size.oh) { return; }
    let index = id.y * size.ow + id.x;
    let m = maps[index];
    let x = i32(m.x << 16u) >> 16; let y = i32(m.x) >> 16;
    let fx = m.y & 31u; let fy = m.y >> 5u;
    let c = (pixel(x,y)*(32u-fx)*(32u-fy) + pixel(x+1,y)*fx*(32u-fy)
           + pixel(x,y+1)*(32u-fx)*fy + pixel(x+1,y+1)*fx*fy + vec4<u32>(512u)) >> vec4<u32>(10u);
    result[index + size.offset] = c.x | (c.y << 8u) | (c.z << 16u) | (255u << 24u);
}
"""


class WGPUProjector:
    def __init__(self, width, height, source_shape, maps, batch_limit=16):
        try:
            import wgpu
            self.wgpu = wgpu
            self.adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
            if self.adapter is None or self.adapter.info.get("adapter_type") not in {"DiscreteGPU", "IntegratedGPU"}:
                raise GPUUnavailable("No hardware WGPU adapter")
            self.device = self.adapter.request_device_sync()
            self.width, self.height = width, height
            sh, sw = source_shape[:2]
            self.source_shape = (sh, sw)
            self.source = self.device.create_texture(size=(sw, sh, 1), format="rgba8uint",
                usage=wgpu.TextureUsage.COPY_DST | wgpu.TextureUsage.TEXTURE_BINDING)
            shader = self.device.create_shader_module(code=SHADER)
            self.pipeline = self.device.create_compute_pipeline(layout="auto", compute={"module": shader, "entry_point": "main"})
            limit = self.device.limits["max-storage-buffer-binding-size"]
            self.batch_size = max(1, min(batch_limit, len(maps), limit // (width*height*4)))
            self.output = self.device.create_buffer(size=width*height*4*self.batch_size,
                usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC)
            self.groups = []
            for index, (mx, my) in enumerate(maps):
                uniform = self.device.create_buffer_with_data(data=np.array([sw, sh, width, height,
                    (index % self.batch_size)*width*height, 0, 0, 0], dtype=np.uint32), usage=wgpu.BufferUsage.UNIFORM)
                fixed, fraction = cv2.convertMaps(mx * (sw-1)/(width-1), my * (sh-1)/(height-1), cv2.CV_16SC2)
                coords = (fixed[..., 0].astype(np.uint16).astype(np.uint32)
                          | (fixed[..., 1].astype(np.uint16).astype(np.uint32) << 16))
                packed = np.stack([coords, fraction.astype(np.uint32)], axis=-1)
                buffer = self.device.create_buffer_with_data(data=packed, usage=wgpu.BufferUsage.STORAGE)
                self.groups.append(self.device.create_bind_group(layout=self.pipeline.get_bind_group_layout(0), entries=[
                    {"binding": 0, "resource": self.source.create_view()},
                    {"binding": 1, "resource": {"buffer": buffer}},
                    {"binding": 2, "resource": {"buffer": self.output}},
                    {"binding": 3, "resource": {"buffer": uniform}},
                ]))
            # Actually execute and read back, rather than trusting GPU inventory.
            self.upload(np.zeros((sh, sw, 3), dtype=np.uint8))
            self.project(0)
        except GPUUnavailable:
            raise
        except Exception as exc:
            if hasattr(self, 'device'):
                self.device.destroy()
            raise GPUUnavailable(str(exc)) from exc

    @property
    def metadata(self):
        info = self.adapter.info
        return {"backend": "WGPU/" + info.get("backend_type", "unknown"),
                "device": info.get("device", "GPU"), "requested": "wgpu", "batch_size": self.batch_size, "fallback_reasons": []}

    def upload(self, image):
        if image.shape[:2] != self.source_shape:
            raise ValueError("Source frame resolution changed within the video")
        try:
            # Retain BGR channel order; shader operates on bytes without color conversion.
            pixels = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
            self.device.queue.write_texture({"texture": self.source}, pixels,
                {"bytes_per_row": image.shape[1]*4, "rows_per_image": image.shape[0]},
                (image.shape[1], image.shape[0], 1))
        except Exception as exc:
            raise GPUUnavailable(str(exc)) from exc

    def project(self, index):
        return next(self.project_many([index]))

    def project_many(self, indices=None):
        indices = list(range(len(self.groups))) if indices is None else list(indices)
        try:
            for start in range(0,len(indices),self.batch_size):
                batch = indices[start:start+self.batch_size]
                if len({index % self.batch_size for index in batch}) != len(batch):
                    raise ValueError("Projection batch contains colliding output slots")
                encoder = self.device.create_command_encoder()
                compute = encoder.begin_compute_pass()
                compute.set_pipeline(self.pipeline)
                for index in batch:
                    compute.set_bind_group(0, self.groups[index])
                    compute.dispatch_workgroups((self.width+15)//16, (self.height+15)//16)
                compute.end()
                self.device.queue.submit([encoder.finish()])
                slots = max(index % self.batch_size for index in batch)+1
                rgba = np.frombuffer(self.device.queue.read_buffer(self.output, size=slots*self.width*self.height*4), dtype=np.uint8).reshape(slots,self.height,self.width,4)
                for index in batch:
                    yield cv2.cvtColor(rgba[index % self.batch_size], cv2.COLOR_BGRA2BGR)
        except Exception as exc:
            raise GPUUnavailable(str(exc)) from exc

    def close(self):
        self.device.destroy()


def create_projector(width, height, source_shape, maps):
    batch = 16
    reasons = []
    while True:
        try:
            result = WGPUProjector(width, height, source_shape, maps, batch_limit=batch)
            return result, reasons
        except GPUUnavailable as exc:
            if batch == 1 or not any(term in str(exc).lower() for term in ('out of memory', 'outofmemory', 'allocation')):
                raise
            batch //= 2
            reasons.append(f'GPU allocation failed; reduced view batch to {batch}')
