"""Validate and copy an interrupted spherical reconstruction before resuming it."""
from contextlib import closing
import json
import math
from pathlib import Path
import shutil
import sqlite3
import struct
import uuid

from reconstruction_preview import pose_input_signature

MODEL_FILES = ('cameras.bin', 'images.bin', 'points3D.bin', 'rigs.bin', 'frames.bin')


def model_count(directory):
    try:
        if not all((directory/name).is_file() and (directory/name).stat().st_size >= 8 for name in MODEL_FILES):
            return 0
        with (directory/'images.bin').open('rb') as handle:
            return struct.unpack('<Q', handle.read(8))[0]
    except OSError:
        return 0


def saved_models(job_dir, view_run_id=None):
    job_dir = Path(job_dir).resolve()
    roots = [job_dir/'spherical_colmap/sparse', job_dir/'spherical_colmap/snapshots']
    for run in (job_dir/'model_runs').glob('*'):
        if view_run_id:
            try:
                record=json.loads((run/'status.json').read_text(encoding='utf8'))
                if record.get('view_run_id') != view_run_id:continue
            except (OSError,ValueError):continue
        roots.extend((run/'recovery/sparse',run/'recovery/snapshots'))
    found = []
    for root in roots:
        for directory in [root, *root.glob('*')]:
            if directory.is_dir() and directory.resolve().is_relative_to(job_dir):
                count = model_count(directory)
                if count >= 3:
                    found.append((count, directory))
    return sorted(found, key=lambda item:(item[0], item[1].stat().st_mtime_ns), reverse=True)


def model_database(model):
    model = Path(model)
    # mapper --input_path writes directly to output_path; a fresh mapper can
    # write output_path/0. Snapshots also have their own numbered subdirectory.
    for parent in (model.parent,model.parent.parent):
        if (parent/'database.db').is_file():
            return parent/'database.db'
    raise ValueError('Missing database belonging to the saved model')


def record_mapper_complete(model, job_dir):
    from task_control import write
    model = Path(model).resolve()
    database = model_database(model)
    if model_count(model) >= 3:
        write(database.parent/'mapper_complete.json',{'input_signature':pose_input_signature(job_dir),
              'model':model.relative_to(database.parent).as_posix(),'registered_positions':model_count(model)})


def plan(job_dir, previous, views):
    job_dir = Path(job_dir).resolve()
    if not previous.get('run_id') or previous.get('stage') != 'model':
        raise ValueError('ไม่พบ Model run เดิมสำหรับกู้ Camera Pose')
    if not previous.get('view_run_id') or previous['view_run_id'] != views.get('run_id'):
        raise ValueError('ชุด Views เปลี่ยนแล้ว ไม่ใช้ pose/database เก่ากับภาพชุดใหม่')
    models = saved_models(job_dir, views.get('run_id'))
    signature = pose_input_signature(job_dir)
    caches = []
    for root in [job_dir/'spherical_colmap', *(job_dir/'model_runs').glob('*/recovery')]:
        try:
            cache = json.loads((root/'stage_cache.json').read_text(encoding='utf8'))
            if cache.get('input_signature') == signature and cache.get('features_complete') and (root/'database.db').is_file():
                caches.append((bool(cache.get('matching_complete')), (root/'stage_cache.json').stat().st_mtime_ns, root, cache))
        except (OSError, ValueError):
            continue
    if not models and not caches:
        raise ValueError('ไม่พบ sparse model / snapshot ที่บันทึกครบสำหรับทำต่อ')
    count, model = models[0] if models else (0, None)
    result = {'schema':1, 'source_run':previous['run_id'], 'input_model':model.relative_to(job_dir).as_posix() if model else None,
            'input_signature':signature, 'registered_positions':count,
            'profile':'balanced', 'previous_status':previous}
    if not model:
        _, _, root, cache = max(caches,key=lambda item:item[:2])
        result.update(database=(root/'database.db').relative_to(job_dir).as_posix(),
                      skip_matching=bool(cache.get('matching_complete')))
    else:
        from task_control import read
        database = model_database(model)
        marker = read(database.parent/'mapper_complete.json')
        result['mapping_complete'] = (marker.get('input_signature') == signature
            and (database.parent/marker.get('model','')).resolve() == model.resolve())
    return result


def record_stage(work, job_dir, stage):
    # Direct CLI callers can supply in-memory metadata. They may run normally,
    # but cannot advertise a durable cache without a stored Views signature.
    if not (Path(job_dir)/'transforms.json').is_file():
        return
    from task_control import read, write
    path = Path(work)/'stage_cache.json'
    cache = read(path)
    write(path, {**cache, 'input_signature':pose_input_signature(job_dir), stage+'_complete':True})


def available(job_dir):
    return bool(saved_models(job_dir) or (Path(job_dir)/'spherical_colmap/stage_cache.json').is_file()
                or list((Path(job_dir)/'model_runs').glob('*/recovery/stage_cache.json')))


def archive_workspace(work_dir):
    """Keep old work on retries/reprocessing instead of silently deleting a checkpoint."""
    work_dir = Path(work_dir).resolve()
    if work_dir.exists():
        destination = work_dir.with_name(work_dir.name+'.archive-'+uuid.uuid4().hex[:12])
        if destination.parent != work_dir.parent or destination == work_dir:
            raise ValueError('Unsafe COLMAP archive destination')
        work_dir.rename(destination)
        return destination


def prepare(job_dir, metadata, recovery, destination, report=lambda message:None):
    """No writes to the original DB/model. A fresh destination owns every output."""
    job_dir = Path(job_dir).resolve(); destination = Path(destination).resolve()
    if not destination.is_relative_to(job_dir/'model_runs') or destination.exists():
        raise ValueError('Recovery destination must be a new directory inside this job run')
    if recovery['input_signature'] != pose_input_signature(job_dir):
        raise ValueError('ภาพ/Camera settings เปลี่ยนหลังขอกู้ pose')
    original = (job_dir/recovery['input_model']).resolve() if recovery.get('input_model') else None
    if original and (not original.is_relative_to(job_dir) or model_count(original) < 3):
        raise ValueError('Invalid recovery model')
    work = job_dir/'spherical_colmap'
    # Each snapshot belongs to its own DB. This also supports a second recovery
    # after interruption without accidentally pairing it with a rebuilt DB.
    database = model_database(original) if original else (job_dir/recovery['database']).resolve()
    if not database.resolve().is_relative_to(job_dir):
        raise ValueError('Invalid recovery database path')
    panorama = work/'panoramas'
    expected = {f'pano_{int(frame["frame_index"]):06d}.jpg' for frame in metadata.get('frames', [])}
    if len(expected) < 3 or not database.is_file():
        raise ValueError('Missing original spherical database or panorama frames')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=1)) as connection:
        connection.execute('PRAGMA query_only=ON')
        image_names = dict(connection.execute('SELECT image_id,name FROM images'))
        names = set(image_names.values())
        if names != expected or any(not (panorama/name).is_file() for name in expected):
            raise ValueError('Panoramas/database do not match the Views frame IDs')
        features = connection.execute('SELECT count(*) FROM keypoints').fetchone()[0]
        matches = connection.execute('SELECT count(*) FROM two_view_geometries WHERE rows>0').fetchone()[0]
        if features != len(expected) or (recovery.get('skip_matching', True) and not matches):
            raise ValueError('Features/matches are incomplete; cannot skip these stages')
    diagnostics = {'registered_positions':0, 'total_positions':len(expected), 'points':0,
                   'features_cached':features, 'matched_pairs_cached':matches}
    if original:
        diagnostics.update(validate_model(original,image_names))
    destination.mkdir(parents=True)
    model_copy = destination/'input_sparse' if original else None
    if original:
        model_copy.mkdir()
        for name in MODEL_FILES:
            shutil.copy2(original/name, model_copy/name)
    report('สำรองฐานข้อมูล features/matches ก่อนทำต่อ')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=1)) as source, \
         closing(sqlite3.connect(destination/'database.db')) as target:
        source.backup(target)
    record = {**recovery, **diagnostics, 'original_model':str(original) if original else None}
    (destination/'recovery.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
    return {'database':destination/'database.db', 'panoramas':panorama, 'model':model_copy,
            'sparse':destination/'sparse', 'snapshots':destination/'snapshots', 'diagnostics':diagnostics,
            'skip_matching':recovery.get('skip_matching',True), 'mapping_complete':bool(recovery.get('mapping_complete'))}


def validate_model(original, image_names):
    import numpy as np
    import pycolmap
    reconstruction = pycolmap.Reconstruction(original)
    if any(image_names.get(image.image_id) != image.name or not np.isfinite(image.projection_center()).all()
           for image in reconstruction.images.values()):
        raise ValueError('Saved camera names/poses are invalid')
    if any(camera.model_name != 'EQUIRECTANGULAR' for camera in reconstruction.cameras.values()):
        raise ValueError('Recovery requires the original spherical camera model')
    if any(not np.isfinite(point.xyz).all() or not math.isfinite(point.error) or point.track.length()<2
           for point in reconstruction.points3D.values()):
        raise ValueError('Saved reconstruction contains invalid or unsupported points')
    diagnostics = {'registered_positions':reconstruction.num_reg_images(), 'points':reconstruction.num_points3D(),
                   'mean_reprojection_error':reconstruction.compute_mean_reprojection_error()}
    if not math.isfinite(diagnostics['mean_reprojection_error']) or diagnostics['mean_reprojection_error'] > 2.5:
        raise ValueError('Saved pose reprojection error exceeds the existing quality limit')
    del reconstruction
    return diagnostics


def mapper_options(snapshots, balanced=False):
    result = ['--Mapper.snapshot_path',str(snapshots),'--Mapper.snapshot_frames_freq','50']
    if balanced:
        # Keep all points and the 50-iteration Ceres default. Reduce repeated global
        # solves and allow convergence at Ceres' usual relative cost tolerance.
        result += ['--Mapper.ba_global_frames_ratio','1.2','--Mapper.ba_global_points_ratio','1.2',
                   '--Mapper.ba_global_function_tolerance','0.000001']
    return result
