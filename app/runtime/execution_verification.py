"""Explicit, bounded CPU/GPU work on generated fixtures; never starts a user Job."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

from app.paths import WORKSPACE
from app.runtime import colmap_ba
from app.runtime.compute_runtime import select_colmap_compute
from app.runtime.runtime_verification import colmap_executable, command_banner


def busy_job(workspace):
    from app.jobs.task_control import identity
    for path in (Path(workspace)/'jobs').glob('*/.task.lock'):
        try:
            record = json.loads(path.read_text(encoding='utf8'))
            if record.get('pid') and identity(record['pid']):
                return path.parent.name
        except (OSError, ValueError, TypeError):
            return path.parent.name
    return None


def views_check(use_gpu):
    import numpy as np
    from app.pipeline.pano_to_views import perspective_map, project_frame
    image = np.random.default_rng(12).integers(0, 256, (64, 128, 3), dtype=np.uint8)
    maps = perspective_map(64, 64, 90, 25, 10)[:2]
    reference = project_frame(image, 64, 64, 90, 25, 10, maps=maps)
    if not use_gpu:
        if reference.shape != (64, 64, 3):
            raise RuntimeError('CPU projection returned an invalid image')
        return dict(actual_backend='CPU', detail='Projected a generated panorama to 64x64')
    from app.runtime.gpu_projection import create_projector
    projector, reasons = create_projector(64, 64, image.shape, [maps])
    try:
        projector.upload(image)
        output = projector.project(0)
        difference = np.abs(output.astype(np.int16)-reference.astype(np.int16))
        mean, p99 = float(difference.mean()), float(np.percentile(difference, 99))
        if mean > .5 or p99 > 2:
            raise RuntimeError(f'GPU projection parity failed: mean={mean}, p99={p99}')
        return dict(actual_backend=projector.metadata['backend'], device=projector.metadata['device'],
                    detail=f'GPU shader/readback passed; CPU pixel difference mean={mean:.4f}, p99={p99:g}',
                    fallback_reasons=reasons)
    finally:
        projector.close()


def masks_check(mode):
    import numpy as np
    import torch
    from torchvision.models.segmentation import LRASPP_MobileNet_V3_Large_Weights, lraspp_mobilenet_v3_large
    from app.runtime.mask_compute import MaskPredictor
    weights = LRASPP_MobileNet_V3_Large_Weights.DEFAULT
    path = Path(torch.hub.get_dir())/'checkpoints'/weights.url.rsplit('/', 1)[-1]
    if not path.is_file() or not hashlib.sha256(path.read_bytes()).hexdigest().startswith(path.stem.rsplit('-', 1)[-1]):
        raise RuntimeError('Cached mask weights missing/corrupt; run the installer first. Verify does not download weights.')
    torch.set_num_threads(min(2, os.cpu_count() or 1))
    predictor = MaskPredictor(lraspp_mobilenet_v3_large(weights=weights).eval(), mode)
    result = predictor.probabilities(torch.zeros(1, 3, 64, 64))
    expected = 'CUDA' if mode.startswith('cuda') else 'DirectML' if mode.startswith('directml') else 'CPU'
    if predictor.backend != expected:
        raise RuntimeError(f'Mask backend fallback: requested {expected}, actually used {predictor.backend}')
    if result.shape != (1, 64, 64) or not np.isfinite(result).all():
        raise RuntimeError('Mask inference returned invalid probabilities')
    return dict(actual_backend=predictor.backend, device=predictor.device_name,
                detail='LRASPP inference passed on one 64x64 image', fallback_reasons=predictor.reasons)


def features_check(executable, directory, environment, use_gpu):
    import cv2
    import numpy as np
    directory.mkdir(parents=True)
    images = directory/'images'
    images.mkdir()
    image = np.random.default_rng(38).integers(0, 256, (320, 640, 3), dtype=np.uint8)
    for index in range(2):
        if not cv2.imwrite(str(images/f'pano_{index}.png'), np.roll(image, index*5, axis=1)):
            raise RuntimeError('Could not write generated feature fixture')
    environment = {**environment, 'COLMAP_DEVICE':'cuda' if use_gpu else 'cpu', 'QT_QPA_PLATFORM':'offscreen'}
    compute = select_colmap_compute(command_banner(executable, ['-h']), environment)
    database = directory/'database.db'
    commands = [
        [str(executable), 'feature_extractor', '--database_path', str(database), '--image_path', str(images),
         '--ImageReader.camera_model', 'EQUIRECTANGULAR', '--ImageReader.single_camera', '1',
         '--SiftExtraction.max_num_features', '512',
         '--FeatureExtraction.max_image_size' if compute.modern else '--SiftExtraction.max_image_size', '640',
         *compute.options('extraction')],
        [str(executable), 'exhaustive_matcher', '--database_path', str(database),
         '--FeatureMatching.max_num_matches', '512', *compute.options('matching')],
    ]
    evidence = []
    for index, command in enumerate(commands):
        label = 'extraction' if index == 0 else 'matching'
        if not use_gpu:
            command += ['--FeatureExtraction.num_threads' if index == 0 else '--FeatureMatching.num_threads', '2']
        command += ['--log_target', 'stdout']
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf8', errors='replace',
                                timeout=45, env=environment)
        log = result.stdout+result.stderr
        path = directory/(label+'.log')
        path.write_text(log, encoding='utf8')
        if result.returncode:
            raise RuntimeError(f'{label} exited {result.returncode}; {log[-1200:]}')
        if use_gpu and ('GPU feature extractor' not in log if index == 0 else 'GPU feature matcher' not in log):
            raise RuntimeError(f'{label} did not confirm GPU execution; see {path}')
        if 'Falling back' in log or 'falling back' in log:
            raise RuntimeError(f'{label} reported CPU fallback; see {path}')
        evidence.append(dict(command=command, log=str(path)))
    with sqlite3.connect(database) as connection:
        keypoints = connection.execute('SELECT sum(rows) FROM keypoints').fetchone()[0] or 0
        matches = connection.execute('SELECT sum(rows) FROM two_view_geometries').fetchone()[0] or 0
    if keypoints < 16 or matches < 8:
        raise RuntimeError(f'Feature fixture produced insufficient work: keypoints={keypoints}, inliers={matches}')
    return dict(actual_backend=compute.backend, detail=f'Extraction + matching passed: {keypoints} features, {matches} inliers',
                evidence=evidence, executable=str(executable))


def ba_benchmark_checks(report, executable, directory, cuda, environment, record=True):
    """Time every eligible global-BA solver on one 360-walk fixture and record the ranking."""
    from app.runtime import ba_solvers
    start = time.monotonic()
    gpu_ready = bool(cuda and colmap_ba.receipt_for(executable))
    try:
        measured = ba_solvers.benchmark(executable, directory, gpu_ready)
    except Exception as exc:
        report['execution_checks'].append(dict(key='ba_benchmark', label='Global BA solver benchmark', requested_backend='CUDA + CPU' if gpu_ready else 'CPU',
                                               actual_backend='unknown', state='failed', seconds=round(time.monotonic()-start, 3), detail=str(exc)))
        return None
    fixture = measured['fixture']
    fastest = measured['ranking'][0] if measured['ranking'] else None
    for row in measured['results']:
        report['execution_checks'].append(dict(
            key='ba_benchmark_'+row['solver'], label='Global BA · '+row['label'], requested_backend=row['requested_backend'],
            actual_backend=row['requested_backend'] if row['ready'] else 'unknown', state='passed' if row['ready'] else 'failed',
            seconds=row.get('seconds', row['wall_seconds']),
            detail=(f"{fixture['frames']} panoramas / {fixture['observations']:,} observations; "
                    f"{int(row.get('iterations', 0))} iterations; cost {row.get('initial_cost', 0):.4f} → {row.get('final_cost', 0):.4f} px"
                    +(' · fastest' if row['solver'] == fastest else '') if row['ready'] else '\n'.join(row['errors']) or f"exit {row['exit_code']}"),
            log=row['log']))
    if record and measured['ready']:
        ba_solvers.write_benchmark(measured)
    try:
        solver, reason = ba_solvers.select(executable, gpu_ready, environment, 1000)
        report['execution_checks'].append(dict(
            key='ba_selection', label='Global BA used by staged mapping (1,000 panoramas)', requested_backend='auto',
            actual_backend=ba_solvers.SOLVERS[solver]['label'], state='passed', seconds=0, detail=reason))
    except Exception as exc:
        report['execution_checks'].append(dict(key='ba_selection', label='Global BA used by staged mapping', requested_backend='auto',
                                               actual_backend='unknown', state='failed', seconds=0, detail=str(exc)))
    return measured


def run_checks(inventory, workspace=None, device='auto'):
    workspace = Path(workspace or WORKSPACE/'web_data').resolve()
    report = dict(execution_requested=True, inventory_ready=inventory['ready'], execution_checks=[],
                  execution_checked_at=datetime.now(timezone.utc).isoformat())
    busy = busy_job(workspace)
    if busy:
        return {**report, 'execution_state':'blocked', 'execution_ready':False,
                'execution_reason':f'Job {busy} is running; retry compute verification after it stops.'}
    directory = workspace/'verification'/('compute-'+datetime.now().strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    report['execution_directory'] = str(directory)
    environment = os.environ.copy()
    if device != 'auto':
        environment.update(COLMAP_DEVICE=device, MASK_DEVICE=device)
    stages = {stage['key']:stage for stage in inventory['stages']}
    hardware_gpu = bool(inventory['gpu']['adapters'] or inventory['gpu']['hardware'])
    cuda = environment.get('COLMAP_DEVICE', 'auto') == 'cuda' or stages.get('colmap', {}).get('backend') == 'CUDA'
    mask_mode = environment.get('MASK_DEVICE', 'auto')
    if mask_mode == 'auto':
        mask_mode = 'cuda' if cuda else 'directml' if os.name == 'nt' and hardware_gpu else 'cpu'

    def record(key, label, requested, operation):
        start = time.monotonic()
        row = dict(key=key, label=label, requested_backend=requested)
        try:
            details = operation()
            if details.get('ready') is False:
                raise RuntimeError(json.dumps(details, ensure_ascii=False))
            row.update(details, state='passed')
        except Exception as exc:
            row.update(state='failed', actual_backend='unknown', detail=str(exc))
        row['seconds'] = round(time.monotonic()-start, 3)
        report['execution_checks'].append(row)

    record('views_cpu', 'Views · CPU', 'CPU', lambda:views_check(False))
    if hardware_gpu or environment.get('VIEW_DEVICE') == 'wgpu':
        if environment.get('VIEW_DEVICE') != 'cpu':
            record('views_gpu', 'Views · GPU shader', 'WGPU', lambda:views_check(True))
    record('masks_cpu', 'Mask inference · CPU', 'CPU', lambda:masks_check('cpu'))
    if mask_mode != 'cpu':
        record('masks_gpu', 'Mask inference · GPU', mask_mode, lambda:masks_check(mask_mode))
    executable = colmap_executable()
    if executable:
        record('features_cpu', 'COLMAP features / matching · CPU', 'CPU',
               lambda:features_check(executable, directory/'features_cpu', environment, False))
        if cuda:
            record('features_gpu', 'COLMAP features / matching · GPU', 'CUDA',
                   lambda:features_check(executable, directory/'features_gpu', environment, True))
        for use_gpu in (False, True) if cuda else (False,):
            # probe() collects separate dense/sparse results, including fallback diagnostics.
            key = 'ba_gpu' if use_gpu else 'ba_cpu'
            try:
                solved = colmap_ba.probe(executable, directory/key, use_gpu=use_gpu)
                for trial in solved['trials']:
                    report['execution_checks'].append(dict(
                        key=key+'_'+trial['solver'], label='COLMAP BA · '+trial['solver'],
                        requested_backend=trial['requested_backend'], actual_backend=trial['actual_backend'],
                        state='passed' if trial['ready'] else 'failed', seconds=trial['seconds'],
                        detail='Spherical solve + finite output model passed' if trial['ready'] else '\n'.join(trial['errors']) or 'Invalid output model',
                        log=trial['log'], command=trial['command']))
            except Exception as exc:
                report['execution_checks'].append(dict(key=key, label='COLMAP BA', requested_backend='CUDA/cuDSS' if use_gpu else 'CPU',
                                                       actual_backend='unknown', state='failed', seconds=0, detail=str(exc)))
        ba_benchmark_checks(report, executable, directory/'ba_benchmark', cuda, environment)
    else:
        record('colmap', 'COLMAP execution', 'COLMAP', lambda:(_ for _ in ()).throw(RuntimeError('COLMAP executable missing')))
    report['execution_checks'].append(dict(key='brush', label='Brush Train', requested_backend='WGPU',
                                           actual_backend='not tested', state='not_tested', seconds=0,
                                           detail='Training was not started. Confirm the selected adapter in the actual training log.'))
    report.update(execution_state='passed' if all(row['state'] != 'failed' for row in report['execution_checks']) else 'failed')
    report['execution_ready'] = report['execution_state'] == 'passed'
    report['execution_scope'] = ('ทดสอบ projection, feature extraction/matching, BA, Mask จริงด้วย fixture เล็ก '
                                 'และจับเวลา Global BA ทุก solver บน fixture 360 องศา (บันทึกผลให้ staged mapping เลือก solver) '
                                 'ไม่เริ่ม Job ไม่เทรน Brush และไม่ทดสอบ VRAM/เสถียรภาพระยะยาว')
    (directory/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return report
