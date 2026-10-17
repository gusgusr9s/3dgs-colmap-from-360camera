"""Original Torch segmentation, with CUDA/DirectML inference and CPU recovery."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import copy

import numpy as np

from compute_runtime import select_mask_device
from gpu_projection import GPUUnavailable
from gpu_worker_io import worker_lines


def mask_mode(requested=None):
    mode = (requested or os.environ.get("MASK_DEVICE", "auto")).strip().lower()
    if not re.fullmatch(r"auto|cpu|cuda(?::\d+)?|directml(?::\d+)?", mode):
        raise ValueError("MASK_DEVICE must be auto, cpu, cuda[:N], or directml[:N]")
    return mode


class MaskPredictor:
    def __init__(self, model, requested=None):
        import torch
        self.torch, self.model = torch, model
        self.active_model = model
        self.mode = mask_mode(requested)
        self.candidates = (["cuda", "directml", "cpu"] if os.name == "nt" else ["cuda", "cpu"]) if self.mode == "auto" else [self.mode]
        self.reasons = []
        self.session = None
        self.backend = None
        self.device_name = "CPU"
        self.device = torch.device("cpu")
        self._advance()

    @property
    def metadata(self):
        return {"backend": self.backend, "device": self.device_name, "requested": self.mode,
                "fallback_reasons": list(self.reasons),
                **({'cpu_runtime':self.cpu_runtime} if hasattr(self,'cpu_runtime') else {})}

    def _advance(self):
        self.session = None
        while self.candidates:
            candidate = self.candidates.pop(0)
            try:
                if candidate.startswith("cuda"):
                    self.device = select_mask_device(self.torch, candidate)
                    self.torch.ones(16, device=self.device).sum().item()
                    self.active_model = copy.deepcopy(self.model).to(self.device)
                    self.backend = "CUDA"
                    self.device_name = self.torch.cuda.get_device_name(self.device)
                elif candidate.startswith("directml"):
                    if os.name != "nt" or importlib.util.find_spec("onnxruntime") is None:
                        raise GPUUnavailable("DirectML requires Windows and onnxruntime-directml")
                    import onnxruntime as ort
                    if "DmlExecutionProvider" not in ort.get_available_providers():
                        raise GPUUnavailable("ONNX Runtime has no DirectML provider")
                    self.device = self.torch.device("cpu")
                    self.model.to(self.device)
                    self.backend = "DirectML"
                    from directml_devices import adapters
                    hardware = [adapter for adapter in adapters() if not adapter['software']]
                    if not hardware:
                        raise GPUUnavailable("No hardware DirectML adapter")
                    selected = (next((adapter for adapter in hardware if adapter['index']==int(candidate.split(':')[1])),None)
                                if ':' in candidate else max(hardware,key=lambda adapter:adapter['video_memory']))
                    if selected is None:
                        raise GPUUnavailable("Requested DirectML adapter does not exist or is software")
                    self.directml_index = selected['index']
                    self.device_name = selected['name']
                else:
                    self.device = self.torch.device("cpu")
                    self.model.to(self.device)
                    self.active_model = self.model
                    self.backend, self.device_name = "CPU", "CPU"
                return
            except (RuntimeError, ImportError) as exc:
                self.reasons.append(f"{candidate}: {exc}")
                if self.mode != "auto":
                    raise GPUUnavailable(str(exc)) from exc
        raise GPUUnavailable("; ".join(self.reasons))

    def _directml_session(self, inputs):
        import onnxruntime as ort
        import torch
        # Hash weights and graph contract, so another model/version cannot reuse stale ONNX.
        signature = hashlib.sha256(f"{torch.__version__}:{type(self.model).__name__}:opset17".encode())
        for key, value in self.model.state_dict().items():
            signature.update(key.encode()); signature.update(value.detach().cpu().numpy().tobytes())
        cache = Path(__file__).resolve().parent / "tools" / "mask_models"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / (signature.hexdigest() + ".onnx")
        if not target.is_file():
            class Output(torch.nn.Module):
                def __init__(self, model):
                    super().__init__(); self.model = model
                def forward(self, images):
                    return self.model(images)["out"]
            with tempfile.NamedTemporaryFile(suffix=".onnx", dir=cache, delete=False) as handle:
                temporary = Path(handle.name)
            try:
                torch.onnx.export(Output(self.model).eval(), inputs[:1], str(temporary), opset_version=17,
                    dynamo=False, input_names=["images"], output_names=["logits"],
                    dynamic_axes={"images": {0: "batch", 2: "height", 3: "width"},
                                  "logits": {0: "batch", 2: "height", 3: "width"}})
                import onnx
                onnx.checker.check_model(str(temporary))
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # Profiling proves inference reached DML rather than silently running on CPU.
        options.enable_profiling = True
        options.profile_file_prefix = str(cache / "probe")
        session = ort.InferenceSession(str(target), sess_options=options,
            providers=[("DmlExecutionProvider", {"device_id": self.directml_index}), "CPUExecutionProvider"])
        sample = inputs[:1].numpy()
        session.run(["logits"], {"images": sample})
        profile_path = Path(session.end_profiling())
        try:
            events = json.loads(profile_path.read_text(encoding="utf-8"))
            if not any(event.get("args", {}).get("provider") == "DmlExecutionProvider" for event in events):
                raise GPUUnavailable("DirectML session executed only CPU operators")
        finally:
            profile_path.unlink(missing_ok=True)
        return session

    def probabilities(self, inputs):
        while True:
            try:
                if self.backend == "DirectML":
                    if self.session is None:
                        self.session = self._directml_session(inputs)
                    logits = self.torch.from_numpy(self.session.run(["logits"], {"images": inputs.numpy()})[0])
                else:
                    with self.torch.inference_mode():
                        logits = self.active_model(inputs.to(self.device))["out"]
                return logits.softmax(1)[:, 15].cpu().numpy()
            except Exception as exc:
                # Recover only inference/backend failures. Export/data programming errors stay visible.
                gpu_failure = isinstance(exc, GPUUnavailable) or any(term in str(exc).lower() for term in
                    ("cuda", "out of memory", "dml", "directml", "device removed", "device lost", "dxgi", "unsupported operator"))
                if self.backend == "CPU" or not gpu_failure or self.mode != "auto":
                    raise
                if "out of memory" in str(exc).lower() and len(inputs) > 1:
                    split = len(inputs)//2
                    return np.concatenate([self.probabilities(inputs[:split]), self.probabilities(inputs[split:])])
                self.reasons.append(f"{self.backend}: {exc}")
                self._advance()


def worker_masks(dataset_dir, progress=None):
    mode = mask_mode()
    candidates = [mode]
    reasons = []
    initial = True
    while candidates:
        selected = candidates.pop(0)
        from cpu_runtime import cpu_budget
        environment = cpu_budget().environment()
        environment.update(PYTHONUTF8='1', MASK_DEVICE=selected)
        result, error, last_backend, timeout_reason = None, None, None, None
        with (Path(dataset_dir) / 'masks.log').open('a', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-u', __file__, str(dataset_dir)],
                stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8', env=environment)
            try:
                with worker_lines(process,label='Masks '+selected) as lines:
                    for line in lines:
                        if not line.startswith('@masks:'):
                            log.write(line); continue
                        event = json.loads(line[len('@masks:'):])
                        if 'progress' in event:
                            last_backend = event['progress']['compute']['backend']
                            if progress:
                                progress(**event['progress'])
                        if 'complete' in event:
                            result = event['complete']
                        if 'error' in event:
                            error = event['error']
                code = process.returncode
            except GPUUnavailable as exc:
                timeout_reason = str(exc)
                code = 82
        if code == 0 and result is not None:
            result['mask_compute']['fallback_reasons'] = reasons + result['mask_compute']['fallback_reasons']
            result['mask_compute']['requested'] = mode
            return result
        # Only native crashes/timeouts enter the outer recovery. Ordinary data errors remain visible.
        if mode != 'auto' or (initial and error):
            raise RuntimeError(error or timeout_reason or f'Mask GPU worker exited with {code}; see masks.log')
        reasons.append(error or timeout_reason or f'Native GPU worker exited with {code}')
        if initial:
            candidates = ['directml', 'cpu'] if os.name == 'nt' and last_backend != 'DirectML' else ['cpu']
            initial = False
    raise RuntimeError('; '.join(reasons))


if __name__ == "__main__":
    from cpu_runtime import cpu_budget
    budget=cpu_budget()
    os.environ.update(budget.environment())
    import torch
    import cv2
    torch.set_num_threads(budget.threads)
    cv2.setNumThreads(budget.threads)
    from spherical_to_brush import create_dynamic_person_masks
    def event(**value):
        print("@masks:" + json.dumps(value), flush=True)
    try:
        result = create_dynamic_person_masks(Path(sys.argv[1]), _gpu_worker=True,
            progress_callback=lambda **value: event(progress=value))
        event(complete=result)
    except Exception as exc:
        event(error=str(exc)); sys.exit(1)
