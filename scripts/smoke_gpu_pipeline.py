"""Create a uniquely named smoke job and exercise the real pipeline to PLY export."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import web_app as app


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,help='source key, e.g. uploads:room.mp4')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reuse-smoke-job',help='Retry a model round of a previous job created by this script')
    args=parser.parse_args()
    source=app.source_from_key(args.source)
    if source is None:raise SystemExit('Source not found')
    if any(app.task_is_active(path.name) for path in app.JOBS.iterdir() if path.is_dir()):
        raise SystemExit('A job is active; wait before running the smoke test')
    os.environ['VIEW_DEVICE']='auto';os.environ['MASK_DEVICE']='auto'
    settings=app.normalized_settings(dict(width=640,height=640,quality='preview',sample_every=30,
                                        start_seconds=0,end_seconds=8,projection='panorama'))
    # Override only this process. Production profile constants and server are unchanged.
    app.QUALITY_PROFILES['preview']={**app.QUALITY_PROFILES['preview'],'steps':2001,'max_resolution':320,'max_splats':50000}
    if args.reuse_smoke_job:
        job=args.reuse_smoke_job
        manifest=app.job_manifest(job)
        if not manifest.get('label','').startswith('GPU pipeline smoke') or manifest.get('source_key')!=args.source or app.views_status(job).get('state')!='ready':
            raise SystemExit('Only an existing ready smoke job for this source may be reused')
        label=manifest['label']
    else:
        job,label=app.create_job_record(source,args.source,settings,'GPU pipeline smoke · DJI')
    print(f'Smoke job {job}: {label}',flush=True)
    if not app.claim_task(job):raise SystemExit('Cannot claim smoke job')
    if args.reuse_smoke_job:
        app.generate_3d(job)
    else:
        app.generate_views(job,args.source,settings,auto_model=True)
    status=app.model_status(job) or {}
    views=app.views_status(job)
    # The CLI must outlive automatic SPZ conversion; the web server normally owns this thread.
    if status.get('state')=='done':
        from model_variants import build_fast_variant
        for record in app.compression_records():
            if record.get('parent_model')==status['model'] and record.get('state') in {'queued','running'}:
                build_fast_variant(app.MODELS/status['model'],record['id'])
    result={'job':job,'views':views,'model':status}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'job':job,'views_state':views.get('state'),'view_compute':views.get('view_compute'),
        'model_state':status.get('state'),'model':status.get('model'),'error':status.get('error'),
        'mask_compute':status.get('quality',{}).get('mask_compute'),'brush':status.get('quality',{}).get('compute_device')},indent=2),flush=True)
    raise SystemExit(0 if status.get('state')=='done' and views.get('state')=='ready' else 1)
