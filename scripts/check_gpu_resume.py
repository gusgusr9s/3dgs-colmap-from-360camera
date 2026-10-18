"""Compare real Brush GPU training with a stopped/resumed run, under the shared queue.

Uses an immutable, small dataset derived from an existing trained model. Never
stops a user job, publishes a model or runs concurrently with an admitted pipeline.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cv2
import numpy as np
from plyfile import PlyData
from app.web import server as app
from app.jobs import task_control
from app.pipeline import training_recovery
from app.jobs.processing_queue import processing_slot


def event(kind,**values):
    print(json.dumps({'at':app.utc_now(),'event':kind,**values},ensure_ascii=True),flush=True)


def fixture(model,run,resolution=128):
    info,parent,seed=app.refinement_input(model)
    original_hash=hashlib.sha256(seed.read_bytes()).hexdigest()
    original_train=app.read_json(parent/'training_input/transforms.json')
    original_val=app.read_json(parent/'training_input/transforms_val.json')
    if not original_train.get('frames') or len(original_val.get('frames',[]))<3:
        raise ValueError('Probe requires the original explicit train/evaluation split')
    dataset=run/'dataset';training=dataset/'training_input'
    (dataset/'images').mkdir(parents=True);(training/'images').mkdir(parents=True)
    shutil.copy2(seed,training/'initial.ply')
    train_indices=np.linspace(0,len(original_train['frames'])-1,min(16,len(original_train['frames'])),dtype=int)
    eval_indices=np.linspace(0,len(original_val['frames'])-1,3,dtype=int)
    groups=[]
    for label,metadata,indices in [('train',original_train,train_indices),('eval',original_val,eval_indices)]:
        frames=[]
        for number,index in enumerate(indices):
            frame=dict(metadata['frames'][int(index)])
            source=(parent/'training_input'/frame['file_path']).resolve()
            if not source.is_relative_to(parent.resolve()):raise ValueError('External probe image')
            image=cv2.imread(str(source));height,width=image.shape[:2]
            ratio=min(resolution/width,resolution/height)
            new_width,new_height=round(width*ratio),round(height*ratio)
            name=f'{label}_{number:03}.png'
            resized=cv2.resize(image,(new_width,new_height),interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(dataset/'images'/name),resized)
            os.link(dataset/'images'/name,training/'images'/name)
            for key,factor in [('fl_x',new_width/width),('fl_y',new_height/height),('cx',new_width/width),('cy',new_height/height)]:
                frame[key]=float(frame.get(key,metadata[key]))*factor
            frame.update(file_path='images/'+name,w=new_width,h=new_height)
            frames.append(frame)
            mask=parent/'training_input/masks'/(source.stem+'.png')
            if mask.is_file():
                (dataset/'masks').mkdir(exist_ok=True);(training/'masks').mkdir(exist_ok=True)
                pixels=cv2.imread(str(mask),cv2.IMREAD_GRAYSCALE)
                cv2.imwrite(str(dataset/'masks'/(Path(name).stem+'.png')),
                            cv2.resize(pixels,(new_width,new_height),interpolation=cv2.INTER_NEAREST))
                os.link(dataset/'masks'/(Path(name).stem+'.png'),training/'masks'/(Path(name).stem+'.png'))
        output={**metadata,'frames':frames,'w':new_width,'h':new_height,'ply_file_path':'initial.ply',
                **{key:frames[0][key] for key in ('fl_x','fl_y','cx','cy')}}
        groups.append(output)
    train,val=groups
    app.write_json(training/'transforms.json',train);app.write_json(training/'transforms_val.json',val)
    app.write_json(dataset/'transforms.json',{**train,'frames':train['frames']+val['frames']})
    split={'mode':'original_held_out_positions','signature':hashlib.sha256(json.dumps(val['frames'],sort_keys=True).encode()).hexdigest(),
           'train_views':len(train['frames']),'eval_views_expected':len(val['frames'])}
    app.write_json(dataset/'evaluation_split.json',split)
    return {'source_model':model,'source_run':info['run_id'],'source_seed':str(seed),'source_sha256':original_hash,
            'train_views':len(train['frames']),'eval_views':len(val['frames']),'evaluation_split':split}


def command(run,steps,every):
    return [str(app.brush_cli()),str(run/'dataset/training_input'),'--total-steps',str(steps),
            '--max-resolution','128','--max-splats','100000','--growth-stop-iter','0','--refine-every','999999',
            '--mean-noise-weight','0','--aux-loss-time','0','--lr-mean','0.000001','--lr-mean-end','0.0000001',
            '--lr-scale','0.001','--lr-scale-end','0.0007','--lr-coeffs-dc','0.001',
            '--eval-every',str(steps),'--eval-save-to-disk','--export-every',str(every),
            '--export-path',str(run/'brush_export'),'--export-name','splat_{iter}.ply']


def run_native(run,cmd,token):
    (run/'brush_export').mkdir(exist_ok=True)
    with task_control.scope(run,token),(run/'3d.log').open('w',encoding='utf8') as log:
        app.run_logged(cmd,stdout=log,stderr=app.subprocess.STDOUT,check=True,cwd=ROOT,
                       env=app.brush_runtime_environment(app.cpu_budget().environment()))


def main(args):
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    job=output/'job';(job/'model_runs').mkdir(parents=True)
    base=job/'model_runs/baseline';base.mkdir()
    quality=fixture(args.model,base)
    interrupted=job/'model_runs/interrupted';interrupted.mkdir()
    shutil.copytree(base/'dataset',interrupted/'dataset')
    base_command=command(base,args.steps,args.every)
    interrupted_command=command(interrupted,args.steps,args.every)
    training_recovery.record(interrupted,interrupted_command,quality)
    event('waiting_for_shared_queue',output=str(output))
    waiting=lambda ahead:event('queued',jobs_ahead=ahead)
    with processing_slot(app.JOBS.parent/'processing_queue',app.task_lock_alive,waiting):
        event('baseline_started')
        run_native(base,base_command,uuid.uuid4().hex)
        baseline=app.evaluate_brush_renders(base/'dataset',base/'brush_export',args.steps)
        app.require_evaluation(baseline)
        adapter=app.read_brush_adapter(base/'3d.log')
        if adapter.get('compute_device_type')=='Cpu' or not adapter.get('compute_device'):
            raise RuntimeError('Probe did not confirm a real GPU adapter: '+str(adapter))
        token=uuid.uuid4().hex;outcomes=[]
        def train():
            try:run_native(interrupted,interrupted_command,token);outcomes.append('finished_without_stop')
            except task_control.TaskStopped:outcomes.append('stopped')
            except Exception as exc:outcomes.append(str(exc))
        event('interrupted_run_started')
        worker=threading.Thread(target=train);worker.start()
        deadline=time.monotonic()+600
        try:
            while worker.is_alive():
                checkpoints=training_recovery.checkpoints(interrupted/'brush_export')
                if checkpoints and checkpoints[0][0]>=args.every:
                    task_control.request_stop(interrupted,token)
                    event('stop_requested',checkpoint=checkpoints[0][0]);break
                if time.monotonic()>deadline:
                    task_control.request_stop(interrupted,token)
                    raise RuntimeError('No checkpoint before timeout')
                time.sleep(.03)
        finally:
            while worker.is_alive():
                worker.join(10)
                if worker.is_alive():event('waiting_for_owned_worker_to_exit')
        if worker.is_alive() or outcomes!=['stopped']:
            raise RuntimeError('Owned probe did not stop at a checkpoint: '+str(outcomes))
        previous={'run_id':'interrupted','view_run_id':'probe'}
        recovery=training_recovery.plan(job,previous,{'run_id':'probe'})
        if not 0<recovery['step']<args.steps:raise ValueError('Probe did not stop before final step')
        continued=job/'model_runs/continued';continued.mkdir()
        resume_command,_,_=training_recovery.prepare(job,recovery,continued)
        event('resume_started',checkpoint=recovery['step'])
        run_native(continued,resume_command,uuid.uuid4().hex)
        resumed=app.evaluate_brush_renders(continued/'dataset',continued/'brush_export',args.steps)
        app.require_evaluation(resumed)
        baseline_ply=training_recovery.final_export(base/'brush_export',args.steps)
        final_ply=training_recovery.final_export(continued/'brush_export',args.steps)
        before=PlyData.read(base/'dataset/training_input/initial.ply')['vertex'].data
        after=PlyData.read(baseline_ply)['vertex'].data
        changed=any(np.max(np.abs(before[key]-after[key]))>1e-10 for key in ('x','y','z','f_dc_0','scale_0'))
        if not changed:raise RuntimeError('Probe did not actually update training weights')
        delta=resumed['eval_psnr_mean_db']-baseline['eval_psnr_mean_db']
        result={'state':'done','gpu':adapter,'fixture':quality,'steps':args.steps,'stopped_checkpoint':recovery['step'],
                'baseline':baseline,'resumed':resumed,'psnr_delta_db':delta,'weights_changed':changed,
                'resume_note':recovery['note'],'baseline_ply':str(baseline_ply),'resumed_ply':str(final_ply),
                'quality_within_1db':abs(delta)<=1,
                'source_unchanged':hashlib.sha256(Path(quality['source_seed']).read_bytes()).hexdigest()==quality['source_sha256'],
                'limits':'Small fine-tuning fixture on this adapter; optimizer/RNG restart, not exact resume or RTX/Ubuntu validation.'}
        app.write_json(output/'result.json',result)
        event('done',psnr_delta_db=delta,quality_within_1db=result['quality_within_1db'])
        if not result['source_unchanged'] or not result['quality_within_1db']:
            raise AssertionError('Source changed or resumed quality differs by more than 1 dB; inspect result.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--steps',type=int,default=4000)
    parser.add_argument('--every',type=int,default=500)
    args=parser.parse_args()
    if not 0<args.every<args.steps:parser.error('Require 0 < every < steps')
    try:main(args)
    except Exception as exc:
        if args.output.is_dir():app.write_json(args.output/'failure.json',{'state':'error','error':str(exc)})
        raise
