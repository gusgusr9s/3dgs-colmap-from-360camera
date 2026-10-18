"""Verified Ceres CUDA/cuDSS bundles and an explicit spherical GPU BA smoke test."""
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from app.paths import ROOT

BUNDLE_NAME = 'colmap-cuda-ba'
RECEIPT_NAME = 'gpu_ba_verified.json'


@lru_cache(maxsize=64)
def _digest(filename, size, modified):
    with Path(filename).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(path):
    path = Path(path)
    stat = path.stat()
    return _digest(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def receipt_for(executable):
    """Read-only inventory. A CUDA banner alone never proves GPU BA support."""
    executable = Path(executable).resolve()
    try:
        root = executable.parent.parent
        receipt = json.loads((root/RECEIPT_NAME).read_text(encoding='utf8'))
        if receipt.get('schema') != 1 or not receipt.get('ready'):
            return None
        fingerprints = receipt.get('files', {})
        if fingerprints.get(executable.relative_to(root).as_posix()) != digest(executable):
            return None
        for name, expected in fingerprints.items():
            candidate = (root/name).resolve()
            if not candidate.is_relative_to(root) or digest(candidate) != expected:
                return None
        return receipt
    except (OSError, ValueError, TypeError):
        return None


def nvidia_visible(environment):
    if os.name == 'nt':
        from app.runtime.directml_devices import adapters
        return any(not adapter['software'] and 'nvidia' in adapter['name'].lower() for adapter in adapters())
    from app.runtime.compute_runtime import nvidia_devices
    return bool(nvidia_devices(environment))


def bundle_executable(root=ROOT, environment=None):
    environment = os.environ if environment is None else environment
    if environment.get('COLMAP_DEVICE', 'auto').lower() == 'cpu':
        return None
    if environment.get('COLMAP_DEVICE', 'auto').lower() == 'auto':
        try:
            profile = json.loads((Path(root)/'tools/install-logs/install-profile.json').read_text(encoding='utf8'))
            if profile.get('colmap_device') == 'cpu':
                return None
        except (OSError, ValueError):
            pass
    executable = Path(root)/'tools'/BUNDLE_NAME/'bin'/('colmap.exe' if os.name == 'nt' else 'colmap')
    if not executable.is_file() or not receipt_for(executable):
        return None
    return executable if nvidia_visible(environment) else None


def mapper_gpu_options(executable, compute, environment):
    mode = environment.get('COLMAP_BA_DEVICE', 'auto').strip().lower()
    if mode not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('COLMAP_BA_DEVICE must be auto, cpu or cuda')
    verified = receipt_for(executable) if mode != 'cpu' and compute.backend == 'CUDA' else None
    enabled = verified is not None
    reason = ('Verified spherical Ceres CUDA dense and cuDSS sparse solvers' if enabled else
              'CPU requested' if mode == 'cpu' or compute.backend == 'CPU' else
              'No verified CUDA/cuDSS BA receipt for this executable')
    if mode == 'cuda' and not enabled:
        raise RuntimeError(reason+'; run the GPU BA installer/probe before requesting COLMAP_BA_DEVICE=cuda')
    options = ['--Mapper.ba_use_gpu', '1', '--Mapper.ba_gpu_index', compute.gpu_index] if enabled else []
    return options, {'colmap_ba_backend': 'CUDA/cuDSS (Ceres)' if enabled else 'CPU (Ceres)',
                     'colmap_ba_device_reason': reason}


def write_fixture(directory):
    """Small, nonzero-residual EQUIRECTANGULAR problem, separate from all Jobs."""
    import pycolmap
    directory = Path(directory)
    text, source = directory/'text', directory/'input'
    text.mkdir(parents=True)
    source.mkdir()
    width, height = 64, 32
    (text/'cameras.txt').write_text(f'1 EQUIRECTANGULAR {width} {height} {width} {height}\n')
    points = [(x*.25, y*.2, 4+z*.5) for x in range(-2, 3) for y in range(-1, 2) for z in range(2)]
    images = []
    for image in range(1, 5):
        shift = (image-2.5)*.15
        pixels = []
        for index, (x, y, z) in enumerate(points):
            x += shift
            radius = math.sqrt(x*x+y*y+z*z)
            u = width*(math.atan2(x, z)/(2*math.pi)+.5)
            v = height*(math.asin(y/radius)/math.pi+.5)
            pixels.append(f'{u+.02*(index%3)} {v} {index+1}')
        images.extend((f'{image} 1 0 0 0 {shift} 0 0 1 pano_{image:06d}.jpg', ' '.join(pixels)))
    (text/'images.txt').write_text('\n'.join(images)+'\n')
    (text/'points3D.txt').write_text(''.join(
        f'{index+1} {x} {y} {z} 100 100 100 .05 '+
        ' '.join(f'{image} {index}' for image in range(1, 5))+'\n'
        for index, (x, y, z) in enumerate(points)))
    pycolmap.Reconstruction(text).write(source)
    return source


def probe(executable, directory, *, use_gpu=True):
    """Exercise real spherical solves; reject CPU fallback when GPU was requested."""
    import pycolmap
    executable = Path(executable).resolve()
    directory = Path(directory).resolve()
    source = write_fixture(directory)
    trials = []
    for kind in (('dense', 'sparse') if use_gpu else ('cpu',)):
        output = directory/kind
        output.mkdir()
        command = [str(executable), 'bundle_adjuster', '--input_path', str(source), '--output_path', str(output),
                   '--BundleAdjustment.backend', 'CERES', '--BundleAdjustmentCeres.use_gpu', str(int(use_gpu)),
                   '--BundleAdjustmentCeres.min_num_images_gpu_solver', '1',
                   '--BundleAdjustmentCeres.max_num_iterations', '3', '--log_target', 'stdout']
        if kind == 'sparse':
            command += ['--BundleAdjustmentCeres.max_num_images_direct_dense_gpu_solver', '0',
                        '--BundleAdjustmentCeres.max_num_images_direct_dense_cpu_solver', '0']
        start = time.monotonic()
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf8', errors='replace',
                                timeout=60, env={**os.environ, 'QT_QPA_PLATFORM':'offscreen'})
        log = result.stdout+result.stderr
        (directory/(kind+'.log')).write_text(log, encoding='utf8')
        errors = [line for line in log.splitlines() if re.search(
            r'Falling back|compiled without|no CUDA GPU|no kernel image|invalid device function|^[EF]\d{8}', line, re.I)]
        valid = False
        if result.returncode == 0 and not errors:
            try:
                model = pycolmap.Reconstruction(output)
                valid = (model.num_reg_images() == 4 and model.num_points3D() == 30
                         and math.isfinite(model.compute_mean_reprojection_error())
                         and all(camera.model_name == 'EQUIRECTANGULAR' for camera in model.cameras.values())
                         and all(all(math.isfinite(value) for value in image.projection_center()) for image in model.images.values())
                         and all(all(math.isfinite(value) for value in point.xyz) for point in model.points3D.values()))
            except (ValueError, RuntimeError):
                pass
        trials.append(dict(solver=kind, ready=valid, exit_code=result.returncode,
                           requested_backend='CUDA/cuDSS' if use_gpu else 'CPU',
                           actual_backend=('CPU' if any('falling back' in error.lower() for error in errors) else
                                           'CUDA/cuDSS' if use_gpu and valid else 'CPU' if valid else 'unknown'),
                           seconds=round(time.monotonic()-start, 3), errors=errors,
                           command=command, log=str(directory/(kind+'.log'))))
    return dict(schema=1, ready=all(trial['ready'] for trial in trials), gpu_requested=use_gpu, executable=str(executable),
                at=datetime.now(timezone.utc).isoformat(), camera_model='EQUIRECTANGULAR', trials=trials)


def write_receipt(executable, report):
    if not report.get('ready') or report.get('gpu_requested') is False:
        raise RuntimeError('GPU BA probe failed; refusing to enable a CPU fallback bundle')
    executable = Path(executable).resolve()
    root = executable.parent.parent
    # Bind the receipt to the solver/runtime DLLs as well as the CLI.
    files = [executable, *executable.parent.glob('*ceres*.dll'), *executable.parent.glob('*cudss*.dll'),
             *executable.parent.glob('*colmap*.dll')]
    receipt = {**report, 'files':{path.relative_to(root).as_posix():digest(path) for path in files}}
    target = root/RECEIPT_NAME
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(target)
    return receipt
