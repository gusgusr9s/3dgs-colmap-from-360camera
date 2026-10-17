"""Read-only environment inventory. Never trains, runs inference or allocates GPU buffers."""
from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig

from compute_runtime import colmap_version, select_colmap_compute, nvidia_devices
from cpu_runtime import cpu_budget

ROOT = Path(__file__).resolve().parent


def executable(name, override=None):
    suffix = '.exe' if os.name == 'nt' else ''
    configured = os.environ.get(override, '') if override else ''
    if configured:
        path = Path(configured).expanduser()
        return str(path / (name + suffix) if path.is_dir() else path)
    local = {'colmap': ROOT / 'tools/colmap/bin', 'brush_app': ROOT / 'tools/brush'}
    candidate = local.get(name, Path(sysconfig.get_path('scripts'))) / (name + suffix)
    found = str(candidate) if candidate.is_file() else shutil.which(name)
    if name == 'brush_app' and not found:
        found = shutil.which('brush')
    return found


def command_banner(command, arguments):
    if not command or not Path(command).is_file():
        raise RuntimeError('ไม่พบโปรแกรม กรุณาติดตั้งด้วย setup script')
    result = subprocess.run([command, *arguments], capture_output=True, text=True,
                            encoding='utf8', errors='replace', timeout=10,
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-600:] or f'exit {result.returncode}')
    return result.stdout + result.stderr


def colmap_executable(modern=True):
    override=os.environ.get('COLMAP_BIN')
    if override:
        candidate=Path(override).expanduser()
        if candidate.is_dir():candidate=candidate/('colmap.exe' if os.name=='nt' else 'colmap')
        if candidate.is_file():return str(candidate)
    names=['colmap','colmap-3.12.4'] if modern else ['colmap-3.12.4','colmap']
    for name in names:
        candidate=ROOT/'tools'/name/'bin'/('colmap.exe' if os.name=='nt' else 'colmap')
        if candidate.is_file():return str(candidate)
    return shutil.which('colmap')


def gpu_inventory():
    hardware, adapters, errors = [], [], []
    if os.name == 'nt':
        try:
            from directml_devices import adapters as dxgi_adapters
            hardware = [a for a in dxgi_adapters() if not a['software']]
        except Exception as exc:
            errors.append('DXGI: ' + str(exc))
    try:
        import wgpu
        # Enumerate adapters only: no request_device, texture, shader or inference.
        adapters = [dict(a.info) for a in wgpu.gpu.enumerate_adapters_sync()
                    if a.info.get('adapter_type') in {'DiscreteGPU', 'IntegratedGPU'}]
    except Exception as exc:
        errors.append('WGPU: ' + str(exc))
    return {'hardware': hardware, 'adapters': adapters, 'errors': errors}


def mask_inventory():
    result = {'cuda': [], 'directml': False, 'errors': []}
    try:
        import torch
        result['torch'] = torch.__version__
        # get_device_name() lazily creates a CUDA context. Inventory must not do that.
        if torch.version.cuda:
            result['cuda'] = nvidia_devices()
            visible=os.environ.get('CUDA_VISIBLE_DEVICES')
            if visible is not None:
                indices=visible.split(',')
                if all(value.strip().isdigit() for value in indices):
                    result['cuda']=[result['cuda'][int(value)] for value in indices if int(value)<len(result['cuda'])]
                elif visible.strip() in {'','-1'}:
                    result['cuda']=[]
            result['cuda_detection']='Torch CUDA build + NVIDIA driver; CUDA context / inference ยังไม่ได้ทดสอบ'
    except Exception as exc:
        result['errors'].append('Torch: ' + str(exc))
    if os.name == 'nt':
        try:
            import onnxruntime as ort
            result['directml'] = 'DmlExecutionProvider' in ort.get_available_providers()
        except Exception as exc:
            result['errors'].append('DirectML: ' + str(exc))
    return result


def verify_environment(workspace=None):
    workspace = Path(workspace or ROOT / 'web_data')
    gpu, masks = gpu_inventory(), mask_inventory()
    packages = {}
    for name in ('numpy', 'opencv-python', 'Pillow', 'plyfile', 'wgpu', 'torch', 'torchvision', 'nerfstudio'):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    if not packages['opencv-python']:
        try:
            packages['opencv-python'] = metadata.version('opencv-python-headless')
        except metadata.PackageNotFoundError:
            pass
    stages = []

    def row(key, label, backend, reason, state='available', **details):
        stages.append(dict(key=key, label=label, backend=backend, reason=reason, state=state, **details))

    parent = workspace
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    free = shutil.disk_usage(parent).free
    writable = os.access(parent, os.W_OK)
    row('upload', 'Upload / บันทึกไฟล์', 'CPU + Disk',
        f'รับไฟล์และบันทึกลงดิสก์ ไม่ใช้ GPU; เหลือ {free / 1024**3:.1f} GB; ตรวจสิทธิ์โดยไม่เขียนไฟล์ทดสอบ',
        'available' if writable else 'error', free_bytes=free, directory=str(workspace))
    ffmpeg = executable('ffmpeg')
    try:
        banner = command_banner(ffmpeg, ['-version']).splitlines()[0]
        row('preview', 'พรีวิวคลิป / ภาพย่อ', 'CPU · FFmpeg / OpenCV',
            'โหลดเฉพาะภาพที่เลือก จำกัด decoder แยกจากคิว Model', executable=ffmpeg, version=banner)
    except Exception as exc:
        row('preview', 'พรีวิวคลิป / ภาพย่อ', 'CPU', str(exc), 'error')
    mode = os.environ.get('VIEW_DEVICE', 'auto').lower()
    view_gpu = bool(gpu['adapters']) and bool(packages['wgpu'])
    backend = 'CPU' if mode == 'cpu' or not view_gpu else 'WGPU · ' + ', '.join(sorted({a['backend_type'] for a in gpu['adapters']}))
    row('views', 'สร้าง Views', backend,
        ('กำหนด CPU ไว้' if mode == 'cpu' else 'พบ hardware adapter; auto ลอง GPU ก่อน แล้ว fallback CPU เมื่อใช้ไม่ได้' if view_gpu
         else 'ไม่พบ hardware WGPU adapter ที่พร้อม จึงใช้ CPU ในโหมด auto; '+'; '.join(gpu['errors'])),
        'error' if mode not in {'auto','cpu','wgpu'} or mode == 'wgpu' and not view_gpu else 'available' if mode == 'cpu' else 'warning')
    colmap = colmap_executable()
    try:
        banner = command_banner(colmap, ['-h'])
        selected = select_colmap_compute(banner)
        if colmap_version(banner) < (4, 1, 0):
            raise RuntimeError('ไฟล์ 360 ต้องใช้ COLMAP 4.1 ขึ้นไป')
        walkthrough=colmap_executable(modern=False)
        alternate_banner=command_banner(walkthrough,['-h']) if walkthrough != colmap else banner
        alternate=select_colmap_compute(alternate_banner)
        row('colmap', 'COLMAP · features / matching / poses', selected.backend,
            selected.reason + '; mapper / bundle adjustment ใช้ค่าของ pipeline ปัจจุบัน ไม่ได้ยืนยันว่าใช้ GPU ทุกส่วน',
            executable=colmap, version=banner.splitlines()[0],
            walkthrough={'executable':walkthrough,'backend':alternate.backend,'version':alternate_banner.splitlines()[0],'reason':alternate.reason})
    except Exception as exc:
        row('colmap', 'COLMAP · features / matching / poses', 'ไม่พร้อม', str(exc), 'error')
    mode = os.environ.get('MASK_DEVICE', 'auto').lower()
    hardware = gpu['hardware'] or gpu['adapters']
    if mode == 'cpu':
        mask_backend, reason = 'CPU', 'กำหนด CPU ไว้'
    elif mode.startswith('cuda') or mode == 'auto' and masks['cuda']:
        mask_backend, reason = 'CUDA', (', '.join(masks['cuda'])+'; '+masks.get('cuda_detection','ยังไม่ได้ทดสอบ inference')
                                       if masks['cuda'] else 'ไม่พบ Torch CUDA build และ NVIDIA driver ที่พร้อม')
    elif mode.startswith('directml') or mode == 'auto' and masks['directml'] and hardware:
        mask_backend, reason = 'DirectML', 'พบ DirectML provider และ hardware adapter'
    else:
        mask_backend, reason = 'CPU', 'ไม่พบ Torch CUDA / DirectML ที่พร้อม; auto fallback CPU'
    valid_mode=bool(re.fullmatch(r'auto|cpu|cuda(?::\d+)?|directml(?::\d+)?',mode))
    index = int(mode.split(':')[1]) if valid_mode and ':' in mode else None
    mask_error = (not packages['torch'] or not packages['torchvision'] or 'torch' not in masks or
                  mode.startswith('cuda') and not masks['cuda'] or
                  mode.startswith('cuda') and index is not None and index >= len(masks['cuda']) or
                  mode.startswith('directml') and not (masks['directml'] and hardware) or
                  mode.startswith('directml') and index is not None and not any(a.get('index') == index for a in gpu['hardware']))
    mask_error=mask_error or not valid_mode
    if mask_error:
        reason += '; runtime หรือ device ที่ระบุยังไม่พร้อม: ' + '; '.join(masks['errors'])
    row('masks', 'Masks · คน / ผู้ถือกล้อง', mask_backend, reason,
        'error' if mask_error else 'warning' if mask_backend != 'CPU' else 'available')
    brush = executable('brush_app', 'BRUSH_CLI')
    try:
        version = command_banner(brush, ['--version']).strip()
        row('model', 'สร้าง Model · Brush', 'GPU · WGPU',
            ('พบ Brush และ hardware adapter; การ์ดที่ Brush เลือกจริงต้องยืนยันจาก training log; ไม่มี CPU training fallback'
             if gpu['adapters'] else 'พบ Brush แต่ไม่พบ hardware WGPU adapter; ยังไม่พร้อม Train; ไม่มี CPU training fallback'),
            'warning' if gpu['adapters'] else 'error', executable=brush, version=version)
    except Exception as exc:
        row('model', 'สร้าง Model · Brush', 'GPU · WGPU', str(exc), 'error')
    row('export', 'ส่งออก PLY / SPZ / GLB', 'CPU',
        'ส่งออกและบีบอัดใช้ CPU; การเปิด 3D ใช้ GPU ของเบราว์เซอร์',
        'available' if executable('node') and packages['plyfile'] else 'error')
    required = ('numpy', 'opencv-python', 'Pillow', 'plyfile', 'torch', 'torchvision', 'nerfstudio')
    missing = [name for name in required if not packages[name]]
    return dict(schema=1, state='done', checked_at=datetime.now(timezone.utc).isoformat(),
                cpu_runtime=cpu_budget().metadata(),
                python=sys.executable, python_version=platform.python_version(), os=platform.platform(),
                gpu=gpu, mask_runtime=masks, packages=packages, stages=stages,
                ready=not missing and all(s['state'] != 'error' for s in stages), missing_packages=missing,
                scope='ตรวจ executable, package, driver และ adapter เท่านั้น ไม่เทรน ไม่ทำ inference ไม่ดาวน์โหลด weights และไม่ทดสอบ VRAM; ผล GPU เป็นความพร้อมเบื้องต้น ไม่ใช่ผลรันจริง')


if __name__ == '__main__':
    print(json.dumps(verify_environment(), ensure_ascii=False))
