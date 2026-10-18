"""Choose the furthest compatible, completed pipeline stage for one Continue button."""
from pathlib import Path
import numpy as np

import colmap_recovery
import dataset_preparation
from reconstruction_preview import pose_input_signature
from task_control import read
import training_recovery


def ready_masks(job,quality):
    if not quality.get('mask_model'):return False
    dataset=Path(job)/'brush_data';metadata=read(dataset/'transforms.json')
    try:
        frames=metadata['frames']
        return bool(frames) and all((dataset/'masks'/(Path(frame['file_path']).stem+'.png')).is_file() for frame in frames)
    except (KeyError,TypeError):return False


def ready_pose_dataset(job,preview):
    job=Path(job).resolve();dataset=job/'brush_data'
    try:
        if not preview or preview.get('input_signature')!=pose_input_signature(job):return False
        metadata=read(dataset/'transforms.json');frames=metadata.get('frames',[])
        if not frames or len(frames)!=len(preview.get('views',[])):return False
        for frame,view in zip(frames,preview['views']):
            relative=Path(frame['file_path']);image=(dataset/relative).resolve()
            if not image.is_relative_to(dataset) or not image.is_file() or image.stat().st_size==0:return False
            if Path(view.get('name') or view.get('file_path','')).name!=relative.name:return False
            if frame.get('frame_index')!=view.get('frame_index'):return False
            left=np.asarray(frame['transform_matrix']);right=np.asarray(view['transform_matrix'])
            if left.shape!=(4,4) or not np.isfinite(left).all() or not np.array_equal(left,right):return False
        if (dataset/'dataset_complete.json').is_file():
            return dataset_preparation.valid_manifest(job,dataset,metadata)
        # Compatibility with complete datasets created before stage receipts:
        # preview is only saved after build/quality checks. Refuse changed source
        # pixels rather than treating their old normalized copies as current.
        for frame in frames:
            image=dataset/frame['file_path'];source=job/image.name
            if source.is_file() and source.stat().st_mtime_ns>image.stat().st_mtime_ns:return False
        seed=(dataset/metadata.get('ply_file_path','sparse_pc.ply')).resolve()
        return seed.is_relative_to(dataset) and seed.is_file() and seed.stat().st_size>0
    except (OSError,KeyError,ValueError,TypeError):return False


def plan(job,previous,views,preview):
    """Never auto-switch to an unrelated model run, profile or Views revision."""
    job=Path(job)
    if previous.get('state') not in {'running','error','stopped'}:
        raise ValueError('งานนี้ไม่ได้หยุดค้าง ให้สร้าง Model ใหม่เมื่อต้องการรุ่นใหม่')
    if previous.get('view_run_id')!=views.get('run_id'):
        raise ValueError('Views เปลี่ยนแล้ว กรุณาสร้าง Model ใหม่จากชุดปัจจุบัน')
    candidate=previous;visited=set();initial=None;reasons=[]
    for _ in range(16):
        identifier=candidate.get('run_id')
        if not identifier or identifier in visited:break
        visited.add(identifier)
        if (candidate.get('view_run_id')!=previous.get('view_run_id')
                or candidate.get('training_options')!=previous.get('training_options')
                or candidate.get('settings')!=previous.get('settings')):break
        if (job/'model_runs'/identifier/'training_command.json').is_file():
            try:
                saved=training_recovery.plan(job,candidate,views)
                return {'kind':'training','label':'Training checkpoint','recovery':saved,'fallback_reasons':reasons}
            except (ValueError,OSError,KeyError) as exc:
                reasons.append(str(exc))
                if initial is None:
                    try:initial=training_recovery.plan(job,candidate,views,allow_initial=True)
                    except (ValueError,OSError,KeyError):pass
        parent=(candidate.get('training_recovery') or {}).get('source_run')
        parent=parent or (candidate.get('colmap_recovery') or {}).get('source_run')
        if not parent:break
        path=(job/'model_runs'/parent/'status.json').resolve()
        if not path.is_relative_to((job/'model_runs').resolve()):break
        candidate=read(path)
    if initial:
        return {'kind':'training','label':'dataset พร้อมเทรน','recovery':initial,'fallback_reasons':reasons}
    if ready_pose_dataset(job,preview):
        masked=ready_masks(job,preview.get('quality',{}))
        return {'kind':'poses','label':'Masks/poses พร้อมแล้ว' if masked else 'ภาพและ poses พร้อมแล้ว',
                'training_options':previous.get('training_options'),'fallback_reasons':reasons}
    saved=colmap_recovery.plan(job,previous,views)
    return {'kind':'colmap','label':'COLMAP ที่บันทึกไว้','recovery':saved,'fallback_reasons':reasons}
