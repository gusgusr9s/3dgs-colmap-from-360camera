"""Global BA solver catalog, measured selection and a spherical benchmark.

COLMAP's mapper hard-codes the Ceres solver thresholds: above 200 frames a CUDA
build uses sparse cuDSS and a CPU build uses CHOLMOD up to 1000 frames.  For 360
sequences the reduced camera system is nearly dense, so on a real 935-panorama
model dense CUDA (39.7 s) and CPU ITERATIVE_SCHUR (46.8 s) reached the same final
cost as sparse cuDSS (79.1 s) and CHOLMOD (94 s).  Only bundle_adjuster exposes
those thresholds, so staged mapping runs it between mapper segments.
"""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from app.paths import ROOT

BENCHMARK_NAME = 'ba-benchmark.json'
# Dense Cholesky is O(n^3) in FP64; consumer GPUs run FP64 at a small fraction
# of FP32, so very large dense systems lose to the sparse solver again.
DENSE_MAX_FRAMES = 3000
# frame pose = 6 tangent parameters; the factor needs a second matrix-sized buffer.
DENSE_BYTES_PER_PARAMETER_PAIR = 8*2
VRAM_RESERVE_MIB = 768

SOLVERS = {
    'cuda_dense': dict(label='CUDA dense (cuSOLVER)', gpu=True, flags=[
        '--BundleAdjustmentCeres.max_num_images_direct_dense_gpu_solver', '3999']),
    'cuda_sparse': dict(label='CUDA sparse (cuDSS)', gpu=True, flags=[]),
    'cpu_iterative': dict(label='CPU iterative Schur', gpu=False, flags=[
        '--BundleAdjustmentCeres.max_num_images_direct_dense_cpu_solver', '1',
        '--BundleAdjustmentCeres.max_num_images_direct_sparse_cpu_solver', '2']),
    'cpu_sparse': dict(label='CPU sparse (CHOLMOD)', gpu=False, flags=[]),
}
DEFAULT_ORDER = ('cuda_dense', 'cpu_iterative', 'cuda_sparse', 'cpu_sparse')
FALLBACK = re.compile(r'Falling back|compiled without|no CUDA GPU|no kernel image|invalid device function|^[EF]\d{8}', re.I | re.M)


def solver_flags(name, gpu_index='-1'):
    solver = SOLVERS[name]
    device = (['--BundleAdjustmentCeres.use_gpu', '1', '--BundleAdjustmentCeres.gpu_index', str(gpu_index)]
              if solver['gpu'] else ['--BundleAdjustmentCeres.use_gpu', '0'])
    return [*device, *solver['flags']]


def dense_vram_mib(frames):
    parameters = 6*frames+16
    return parameters*parameters*DENSE_BYTES_PER_PARAMETER_PAIR/1024**2


def free_vram_mib(environment=None, gpu_index='-1'):
    environment = os.environ if environment is None else environment
    executable = shutil.which('nvidia-smi', path=environment.get('PATH', ''))
    if not executable:
        return None
    command = [executable, '--query-gpu=memory.free', '--format=csv,noheader,nounits']
    if str(gpu_index) not in {'', '-1'}:
        command += ['-i', str(gpu_index)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, env=environment)
        values = [float(line) for line in result.stdout.split() if line.strip()]
        return max(values) if result.returncode == 0 and values else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def benchmark_path(root=ROOT):
    return Path(root)/'tools/install-logs'/BENCHMARK_NAME


def measured_ranking(executable, root=ROOT):
    """Fastest-first solver names measured for this exact executable, or None."""
    from app.runtime.colmap_ba import digest
    try:
        record = json.loads(benchmark_path(root).read_text(encoding='utf8'))
        if record.get('schema') != 1 or record.get('executable_sha256') != digest(executable):
            return None
        return [name for name in record.get('ranking', []) if name in SOLVERS] or None
    except (OSError, ValueError, TypeError):
        return None


def select(executable, gpu_ready, environment=None, frames=0, gpu_index='-1', root=ROOT):
    """Return (solver, reason). gpu_ready means a verified CUDA/cuDSS receipt and a CUDA compute choice."""
    environment = os.environ if environment is None else environment
    mode = environment.get('COLMAP_BA_SOLVER', 'auto').strip().lower()
    if mode != 'auto' and mode not in SOLVERS:
        raise ValueError('COLMAP_BA_SOLVER must be auto, '+', '.join(SOLVERS))
    if mode != 'auto':
        if SOLVERS[mode]['gpu'] and not gpu_ready:
            raise RuntimeError(f'COLMAP_BA_SOLVER={mode} needs the verified CUDA/cuDSS COLMAP bundle')
        return mode, 'requested by COLMAP_BA_SOLVER'
    ranking = measured_ranking(executable, root)
    order = ranking or list(DEFAULT_ORDER)
    skipped = []
    for name in order:
        if SOLVERS[name]['gpu'] and not gpu_ready:
            continue
        if name == 'cuda_dense':
            if frames > DENSE_MAX_FRAMES:
                skipped.append(f'cuda_dense skipped: {frames} frames > {DENSE_MAX_FRAMES}')
                continue
            free = free_vram_mib(environment, gpu_index)
            needed = dense_vram_mib(frames)+VRAM_RESERVE_MIB
            if free is None or free < needed:
                skipped.append(f'cuda_dense skipped: needs ~{needed:.0f} MiB, free {free if free is not None else "unknown"} MiB')
                continue
        basis = 'fastest measured by the installer/Verify benchmark' if ranking else 'default order (no benchmark for this executable)'
        return name, '; '.join([basis, *skipped])
    return 'cpu_sparse', '; '.join(['no faster solver available', *skipped])


def write_benchmark_fixture(directory, frames=360, points_per_frame=120, seed=7):
    """A 360 walk: one EQUIRECTANGULAR camera, short local tracks plus long far-field tracks
    (which make the reduced camera system dense, as in real panoramas), noisy initial state."""
    import numpy as np
    import pycolmap
    directory = Path(directory)
    text, source = directory/'text', directory/'input'
    text.mkdir(parents=True)
    source.mkdir()
    width, height = 1920, 960
    camera = pycolmap.Camera(model='EQUIRECTANGULAR', width=width, height=height, params=[width, height])
    rng = np.random.default_rng(seed)
    spacing = .1
    centers = np.stack([np.arange(frames)*spacing, .05*np.sin(np.arange(frames)*.2), np.zeros(frames)], 1)
    count = frames*points_per_frame
    reach = np.where(rng.random(count) < .2, 6., .75)
    xyz = np.stack([rng.uniform(-.75, centers[-1, 0]+.75, count), rng.uniform(-3, 3, count), rng.uniform(-3, 3, count)], 1)
    keep = np.linalg.norm(xyz[:, 1:], axis=1) > 1
    xyz, reach = xyz[keep], reach[keep]

    def visible_points():
        return [np.flatnonzero(np.abs(xyz[:, 0]-center[0]) < reach) for center in centers]
    seen = np.bincount(np.concatenate(visible_points()), minlength=len(xyz))
    xyz, reach = xyz[seen >= 2], reach[seen >= 2]
    visible = visible_points()
    observations = [[] for _ in range(frames)]
    tracks = [[] for _ in range(len(xyz))]
    for frame, (center, indices) in enumerate(zip(centers, visible)):
        pixels = camera.img_from_cam(xyz[indices]-center)+rng.normal(0, .5, (len(indices), 2))
        for point, (u, v) in zip(indices, pixels):
            tracks[point].append((frame+1, len(observations[frame])))
            observations[frame].append(f'{u:.3f} {v:.3f} {point+1}')
    (text/'cameras.txt').write_text(f'1 EQUIRECTANGULAR {width} {height} {width} {height}\n')
    noisy = centers+rng.normal(0, .003, centers.shape)
    noisy[:2] = centers[:2]  # Keep the gauge/scale reference intact.
    (text/'images.txt').write_text(''.join(
        f'{frame+1} 1 0 0 0 {-x:.6f} {-y:.6f} {-z:.6f} 1 pano_{frame:06d}.jpg\n{" ".join(observations[frame])}\n'
        for frame, (x, y, z) in enumerate(noisy)))
    perturbed = xyz+rng.normal(0, .005, xyz.shape)
    (text/'points3D.txt').write_text(''.join(
        f'{index+1} {x:.6f} {y:.6f} {z:.6f} 128 128 128 1 '+' '.join(f'{image} {slot}' for image, slot in track)+'\n'
        for index, ((x, y, z), track) in enumerate(zip(perturbed, tracks))))
    pycolmap.Reconstruction(text).write(source)
    return source, dict(frames=frames, points=len(tracks), observations=sum(map(len, tracks)))


def parse_report(log):
    values = {}
    for key, pattern in (('seconds', r'Time\s*:\s*([\d.]+)'), ('final_cost', r'Final cost\s*:\s*([\d.eE+-]+)'),
                         ('initial_cost', r'Initial cost\s*:\s*([\d.eE+-]+)'), ('iterations', r'Iterations\s*:\s*(\d+)')):
        match = re.search(pattern, log)
        if match:
            values[key] = float(match.group(1))
    return values


def benchmark(executable, directory, gpu_ready, gpu_index='-1', frames=360, timeout=240):
    """Run every eligible solver on one fixture; rank by time among results matching the best cost."""
    from app.runtime.cpu_runtime import cpu_budget
    executable, directory = Path(executable).resolve(), Path(directory).resolve()
    source, fixture = write_benchmark_fixture(directory/'fixture', frames=frames)
    # Same native thread budget as a Job (Verify itself runs with OMP_NUM_THREADS=1).
    environment = {**cpu_budget().environment(), 'QT_QPA_PLATFORM': 'offscreen'}
    results = []
    for name in DEFAULT_ORDER:
        if SOLVERS[name]['gpu'] and not gpu_ready:
            continue
        output = directory/name
        output.mkdir()
        command = [str(executable), 'bundle_adjuster', '--input_path', str(source), '--output_path', str(output),
                   '--BundleAdjustment.backend', 'CERES', *solver_flags(name, gpu_index),
                   '--BundleAdjustmentCeres.max_num_iterations', '50',
                   '--BundleAdjustmentCeres.function_tolerance', '0.000001', '--log_target', 'stdout']
        started = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf8', errors='replace',
                                    timeout=timeout, env=environment)
            log, code = result.stdout+result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            log, code = f'timeout after {timeout}s\n{exc.stdout or ""}', -1
        (directory/(name+'.log')).write_text(log, encoding='utf8')
        report = parse_report(log)
        errors = [line for line in log.splitlines() if FALLBACK.search(line)]
        ready = code == 0 and not errors and math.isfinite(report.get('final_cost', math.nan))
        results.append(dict(solver=name, label=SOLVERS[name]['label'], requested_backend='CUDA' if SOLVERS[name]['gpu'] else 'CPU',
                            ready=ready, exit_code=code, errors=errors, wall_seconds=round(time.monotonic()-started, 3),
                            **report, log=str(directory/(name+'.log'))))
    costs = [row['final_cost'] for row in results if row['ready']]
    best = min(costs) if costs else math.nan
    for row in results:
        # Same problem, same tolerance: a solver that stops at a worse cost is not a valid speedup.
        if row['ready'] and row['final_cost'] > best*1.01+1e-9:
            row['ready'] = False
            row['errors'] = [f'final cost {row["final_cost"]} worse than best {best}']
    ranking = [row['solver'] for row in sorted((row for row in results if row['ready']), key=lambda row: row['seconds'])]
    from app.runtime.colmap_ba import digest
    return dict(schema=1, at=datetime.now(timezone.utc).isoformat(), executable=str(executable),
                executable_sha256=digest(executable), gpu_requested=gpu_ready, fixture=fixture,
                results=results, ranking=ranking, ready=bool(ranking))


def write_benchmark(report, root=ROOT):
    if not report.get('ready'):
        raise RuntimeError('BA benchmark found no valid solver')
    target = benchmark_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(target)
    return target
