"""Durable Brush weight checkpoints; optimizer state is explicitly not restored."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

import numpy as np

from task_control import read, write

INTERVAL = 5000


def checkpoint_valid(path):
    """Reject incomplete exports, non-Gaussian data and nonfinite parameters."""
    try:
        with Path(path).open('rb') as handle:
            header = []
            while handle.tell() < 65536:
                line = handle.readline()
                if not line:
                    return False
                header.append(line)
                if line.strip() == b'end_header':
                    break
            else:
                return False
            offset = handle.tell()
        text = b''.join(header).decode('ascii')
        count = re.search(r'^element vertex (\d+)$', text, re.M)
        properties = re.findall(r'^property float (\S+)$', text, re.M)
        required = {'x','y','z','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3','f_dc_0','f_dc_1','f_dc_2'}
        if 'format binary_little_endian 1.0' not in text or not count or not required.issubset(properties):
            return False
        count = int(count[1])
        if count < 1 or Path(path).stat().st_size != offset + count*len(properties)*4:
            return False
        values = np.memmap(path, dtype='<f4', mode='r', offset=offset, shape=(count,len(properties)))
        try:
            for start in range(0,count,65536):
                batch = values[start:start+65536]
                if not np.isfinite(batch).all():
                    return False
                if (batch[:,[properties.index('scale_'+str(i)) for i in range(3)]] > 80).any():
                    return False
                rotation = batch[:,[properties.index('rot_'+str(i)) for i in range(4)]].astype(np.float64)
                if (np.linalg.norm(rotation,axis=1) <= 1e-12).any():
                    return False
            return True
        finally:
            del values
    except (OSError, ValueError, UnicodeError):
        return False


def checkpoints(export, validate=True):
    result = []
    for path in Path(export).glob('splat_*.ply'):
        match = re.fullmatch(r'splat_(\d+)\.ply', path.name)
        if match and (not validate or checkpoint_valid(path)):
            result.append((int(match[1]),path))
    return sorted(result, reverse=True)


def prune(export, keep=2):
    # Caller owns this run. A partial newest export never evicts a complete one.
    for _, path in checkpoints(export)[keep:]:
        path.unlink(missing_ok=True)


def final_export(export, steps):
    target = Path(export)/'splat.ply'
    checkpoint = Path(export)/f'splat_{steps:0{len(str(steps))}d}.ply'
    if checkpoint.is_file():
        if not checkpoint_valid(checkpoint):
            raise ValueError('Brush checkpoint ล่าสุดเขียนไม่ครบหรือมีค่าผิดปกติ')
        temporary = target.with_name('.final-'+uuid.uuid4().hex+'.tmp')
        try:
            try:
                os.link(checkpoint,temporary)
            except OSError:
                shutil.copy2(checkpoint,temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def dataset_signature(dataset):
    dataset = Path(dataset).resolve()
    digest = hashlib.sha256()
    for path in sorted(dataset.rglob('*')):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(dataset):
            raise ValueError('Training dataset contains an external path')
        digest.update(path.relative_to(dataset).as_posix().encode())
        if path.suffix == '.json':
            digest.update(path.read_bytes())
        else:
            stamp = path.stat()
            digest.update(f'{stamp.st_size}:{stamp.st_mtime_ns}'.encode())
    return digest.hexdigest()


def record(run, command, quality):
    run = Path(run)
    write(run/'training_command.json', {'command':list(map(str,command)), 'quality':quality,
                                       'dataset_signature':dataset_signature(run/'dataset'),
                                       'resume_note':'PLY weights and iteration reused; optimizer and RNG restart.'})


def plan(job, previous, views, allow_initial=False):
    job = Path(job).resolve()
    run = (job/'model_runs'/previous.get('run_id','')).resolve()
    if not run.is_relative_to(job/'model_runs') or run == job/'model_runs':
        raise ValueError('Invalid training run')
    if previous.get('view_run_id') != views.get('run_id'):
        raise ValueError('Views เปลี่ยนแล้ว ไม่ใช้ checkpoint ของงานเก่ากับชุดใหม่')
    saved = read(run/'training_command.json')
    if not saved or saved['dataset_signature'] != dataset_signature(run/'dataset'):
        raise ValueError('ชุดภาพ/Mask/config เปลี่ยนแล้ว ไม่ใช้ checkpoint เก่ากับข้อมูลใหม่')
    target = int(saved['command'][saved['command'].index('--total-steps')+1])
    candidates = [(step,path) for step,path in checkpoints(run/'brush_export') if 0 < step <= target]
    if not candidates:
        if allow_initial:
            command=saved['command']
            step=int(command[command.index('--start-iter')+1]) if '--start-iter' in command else 0
            return {'source_run':previous['run_id'],'step':step,'checkpoint':None,
                    'dataset_signature':saved['dataset_signature'],
                    'note':'Prepared dataset/split reused; training restarts from its saved initialization.'}
        raise ValueError('ยังไม่มี Brush checkpoint ที่เขียนครบ ให้ทำต่อจาก Camera Pose แทน')
    step, path = candidates[0]
    return {'source_run':previous['run_id'], 'step':step, 'checkpoint':path.relative_to(job).as_posix(),
            'dataset_signature':saved['dataset_signature'], 'note':saved['resume_note']}


def prepare(job, recovery, run):
    job, run = Path(job).resolve(), Path(run).resolve()
    parent = (job/'model_runs'/recovery['source_run']).resolve()
    checkpoint = (job/recovery['checkpoint']).resolve() if recovery.get('checkpoint') else None
    if (not parent.is_relative_to(job/'model_runs') or parent == job/'model_runs'
            or (checkpoint is not None and not checkpoint.is_relative_to(parent/'brush_export'))
            or not run.is_relative_to(job/'model_runs') or run == job/'model_runs' or (run/'dataset').exists()):
        raise ValueError('Unsafe training recovery path')
    saved = read(parent/'training_command.json')
    if (dataset_signature(parent/'dataset') != recovery['dataset_signature']
            or saved.get('dataset_signature') != recovery['dataset_signature'] or (checkpoint is not None and not checkpoint_valid(checkpoint))):
        raise ValueError('Training checkpoint/dataset เปลี่ยนหรือเขียนไม่ครบ')
    def copy(source, target):
        if Path(source).suffix == '.json':
            return shutil.copy2(source, target)
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        return target
    shutil.copytree(parent/'dataset', run/'dataset', copy_function=copy)
    training = run/'dataset/training_input'
    if checkpoint is not None:
        shutil.copy2(checkpoint, training/'resume_init.ply')
        for name in ('transforms.json','transforms_val.json'):
            metadata = read(training/name)
            metadata['ply_file_path'] = 'resume_init.ply'
            write(training/name, metadata)
    command = list(saved['command'])
    command[1] = str(training)
    command[command.index('--export-path')+1] = str(run/'brush_export')
    if '--start-iter' in command:
        command[command.index('--start-iter')+1] = str(recovery['step'])
    else:
        command.extend(['--start-iter',str(recovery['step'])])
    return command, saved['quality'], parent
