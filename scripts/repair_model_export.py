"""Republish saved checkpoints with numerical validation, preserving old versions."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import server as app


def repair(model):
    if Path(model).name != model or not (app.MODELS/model).is_file():
        raise ValueError('Model does not exist')
    info = app.model_info(app.MODELS/model)
    job, parent_run = info.get('job'), info.get('run_id')
    directory = app.job_directory(job or '')
    if directory is None or not app.re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{8}', parent_run or ''):
        raise ValueError('Model has no saved training run')
    parent = directory/'model_runs'/parent_run
    checkpoint = parent/'brush_export/splat.ply'
    dataset = parent/'dataset'
    if not (dataset/'transforms.json').is_file():
        # Legacy versions can be recovered only while their root dataset is
        # still exactly the same capture, with no per-run selection ambiguity.
        dataset = directory/'brush_data'
        frames = app.read_json(dataset/'transforms.json',{}).get('frames',[])
        if (info.get('view_run_id') != app.views_status(job).get('run_id')
                or info.get('input_signature') != app.pose_input_signature(directory)
                or info.get('selection')
                or len(frames) != info.get('quality',{}).get('perspective_views')):
            raise ValueError('Legacy input no longer matches the saved model')
    if not checkpoint.is_file():
        raise ValueError('Saved raw checkpoint does not exist')
    if not app.claim_task(job):
        raise RuntimeError('Job is busy')
    timer = app.TaskTimer()
    try:
        identifier = app.allocate_model_run(job)
        run = directory/'model_runs'/identifier

        def copy_immutable(source,target):
            if Path(source).suffix.lower() != '.json':
                try:
                    os.link(source,target)
                    return target
                except OSError:
                    pass
            return shutil.copy2(source,target)

        shutil.copytree(dataset,run/'dataset',copy_function=copy_immutable)
        export = run/'brush_export'
        export.mkdir()
        copy_immutable(checkpoint,export/'splat.ply')
        # The parent evaluation already renders this exact raw checkpoint.
        for renders in (parent/'brush_export').glob('eval_*'):
            if renders.is_dir():
                shutil.copytree(renders,export/renders.name,copy_function=copy_immutable)
        quality = dict(info.get('quality',{}))
        quality.update(app.sanitize_splat_ply(checkpoint,export/'validated.ply'))
        quality.update(app.evaluate_brush_renders(run/'dataset',export,quality['training_steps']))
        quality['reexported_without_training'] = True
        name = f'{job}_{identifier}.ply'
        (export/'validated.ply').replace(app.MODELS/name)
        record = {**info, **timer.snapshot(finished=True), 'run_id':identifier,
                  'created_at':app.utc_now(),
                  'job':job,'model':name,'state':'done','stage':'model','progress':100,
                  'step':'Re-exported saved checkpoint; learned geometry preserved',
                  'operation':'validated_reexport','reexported_from':model,
                  'parent_run_id':parent_run,'quality':quality,
                  'viewer_config':app.dataset_viewer_config(directory,run/'dataset')}
        # This operation is export repair, not an additional training run.
        record.pop('refined_from',None)
        record.pop('refinement_plan',None)
        record.pop('refinement_note',None)
        app.write_json(run/'status.json',record)
        app.write_json(directory/'3d_status.json',record)
        app.write_json(app.model_metadata_path(name),record)
        return record
    finally:
        app.release_task(job)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model',action='append',required=True)
    args = parser.parse_args()
    for model in args.model:
        result = repair(model)
        print(json.dumps({k:result[k] for k in ('model','job','run_id','reexported_from','quality')}),flush=True)
