"""Independent 1920x960 walking-video pilot, with fresh spherical COLMAP poses.

Prepare poses on CPU while the controlled comparisons train. Wait for their
worker to finish before using the GPU, then publish through the normal checks.
"""
from pathlib import Path
import sys
import time
import uuid
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.web import server as w


def main():
    report_file = w.EXPERIMENTS / 'aist_low_resolution_pilot.json'
    if report_file.exists(): raise ValueError('Pilot already exists; preserve it')
    key = 'data/raw:aist_living_lab_1.mp4'
    source = w.source_from_key(key)
    if source is None: raise ValueError('Download the AIST sample first')
    settings = {**w.DEFAULT_VIEW_SETTINGS, 'width':448,'height':448,'sample_every':8,
                'projection':'panorama','quality':'high','start_seconds':0,'end_seconds':45}
    job = uuid.uuid4().hex[:10]
    (w.JOBS / job).mkdir()
    label = 'AIST Living Lab 1920x960 - independent 45s walking pilot'
    w.write_json(w.JOBS/job/'job.json',dict(label=label,source_key=key,source_name=source.name,settings=settings))
    case = dict(id='aist_45s',job=job,label=label,source_key=key,settings=settings,
                profile=w.probe_source(source),plan=w.sampling_plan(w.probe_source(source),settings),state='running')
    report = dict(batch=report_file.stem,started_at=w.utc_now(),cases=[case])
    w.write_json(report_file,report)
    try:
        import torch
        torch.set_num_threads(8);torch.set_num_interop_threads(2)
        if not w.claim_task(job): raise RuntimeError('Job busy')
        w.generate_views(job,key,settings)
        if w.views_status(job).get('state')!='ready': raise RuntimeError('Views failed')
        manifest=w.read_json(w.JOBS/job/'job.json')
        manifest['auto_model']=True  # Use the normal all-registered seed/mask filter for training.
        w.write_json(w.JOBS/job/'job.json',manifest)
        if not w.claim_task(job): raise RuntimeError('Job busy')
        w.generate_3d(job,poses_only=True)
        if w.read_json(w.JOBS/job/'pose_status.json').get('state')!='ready':
            raise RuntimeError(w.read_json(w.JOBS/job/'pose_status.json').get('error','Pose recovery failed'))
        print('POSES READY '+job,flush=True)
        while True:
            comparison=w.read_json(w.EXPERIMENTS/'concourse_resolution_comparison.json')
            if comparison.get('finished_at') or any(c.get('state')=='ERROR' for c in comparison.get('cases',[])): break
            time.sleep(5)
        # Also serialize with any additional training launched by the user.
        while any(w.task_is_active(p.name) for p in w.JOBS.iterdir() if p.is_dir()): time.sleep(5)
        if not w.claim_task(job): raise RuntimeError('Job busy')
        identifier=w.allocate_model_run(job);case['run_id']=identifier;w.write_json(report_file,report)
        print('TRAINING '+job,flush=True)
        w.generate_3d(job,identifier=identifier)
        status=w.model_status(job)
        if status.get('state')!='done': raise RuntimeError(status.get('error','Training failed'))
        case.update(state='done',model=status['model'],quality=status['quality'],status=status)
    except Exception as exc:
        case.update(state='error',error=str(exc));raise
    finally:
        w.release_task(job)
        report['finished_at']=w.utc_now();w.write_json(report_file,report)


if __name__=='__main__':main()
