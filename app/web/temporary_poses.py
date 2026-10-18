"""Disposable five-second camera maps, outside the persistent Jobs library."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from app.jobs.processing_queue import processing_slot, try_lock
from app.jobs.task_control import read, write
from app.paths import ROOT
from app.pipeline.capture_preview import five_second_settings, sampling_plan
from app.runtime.python_command import module_command
from app.runtime.tool_environment import tool_environment

TTL_SECONDS = 3600
MAX_CACHED = 16


def directory(cache, identifier):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('Invalid temporary preview ID')
    root = Path(cache).resolve()
    path = (root/identifier).resolve()
    if path.parent != root:
        raise ValueError('Invalid temporary preview directory')
    return path


def cleanup(cache):
    root = Path(cache).resolve()
    if not root.exists():
        return
    candidates = sorted((p for p in root.iterdir() if re.fullmatch(r'[a-f0-9]{32}',p.name)),
                        key=lambda p:p.stat().st_mtime, reverse=True)
    for index, candidate in enumerate(candidates):
        path = directory(root, candidate.name)  # Verify absolute deletion boundary.
        record = read(path/'status.json')
        if time.time()-record.get('at',time.time()) < TTL_SECONDS and index < MAX_CACHED:
            continue
        try:
            with (path/'.lock').open('a+b') as handle:
                if not handle.tell():
                    handle.write(b'0');handle.flush()
                if not try_lock(handle):
                    continue
                for child in path.iterdir():
                    if child.name in {'.lock','status.json'}:
                        continue
                    if child.is_symlink():
                        child.unlink()
                    elif child.resolve().is_relative_to(path):
                        shutil.rmtree(child) if child.is_dir() else child.unlink()
                write(path/'status.json',dict(state='expired',step='พรีวิวชั่วคราวหมดอายุ กรุณาคำนวณใหม่',at=time.time()))
        except (FileNotFoundError, PermissionError):
            continue


def status(cache, identifier):
    path = directory(cache,identifier)
    record = read(path/'status.json')
    if not record:
        return dict(id=identifier,state='missing',error='ไม่พบพรีวิวชั่วคราว กรุณาคำนวณใหม่จาก Source')
    if record.get('state') in {'queued','running'}:
        try:
            with (path/'.lock').open('r+b') as handle:
                abandoned = try_lock(handle)
        except FileNotFoundError:
            abandoned = True
        if abandoned:
            record = dict(state='error',error='พรีวิวถูกหยุด กรุณาคำนวณใหม่')
    if record.get('state') == 'ready' and time.time()-record.get('at',0) >= TTL_SECONDS:
        record = dict(state='expired',error='พรีวิวหมดอายุ กรุณาคำนวณใหม่')
    result = dict(id=identifier,**record,busy=record.get('state') in {'queued','running'},training_ready=False)
    if result['state'] == 'ready':
        preview = read(path/'preview.json')
        if preview:
            from app.web.spacing_diagnostics import diagnostics
            preview['spacing'] = diagnostics(path,preview,Path(cache).parent.parent/'jobs')
            result['preview'] = preview
        else:
            result.update(state='error',error='ไม่พบข้อมูลแผนที่ กรุณาคำนวณใหม่')
    result['status'] = {key:result[key] for key in ('state','step','error','progress') if key in result}
    return result


def start(source, profile, settings, cache, queue, owner_alive, seconds=5):
    source = Path(source).resolve()
    if seconds not in (5,10):
        raise ValueError('ช่วงทดสอบชั่วคราวต้องเป็น 5 หรือ 10 วินาที')
    clipped = five_second_settings(profile,settings)
    original = sampling_plan(profile,settings)
    clipped['end_seconds'] = min(original['end_seconds'],float(settings.get('start_seconds',0))+seconds)
    plan = sampling_plan(profile,clipped)
    if not plan['panorama'] or (plan['positions'] or 0) < 6:
        raise ValueError('พรีวิวแผนที่ต้องเป็นวิดีโอ 360 ที่มีอย่างน้อย 6 ตำแหน่ง ลองเลือกทดสอบ 10 วินาทีหรือลดจำนวนเฟรมที่ข้าม')
    # Pose uses the usual native panorama size. Only inspector crops are smaller.
    scale = min(1,640/max(clipped['width'],clipped['height']))
    effective = {**clipped,'width':max(1,round(clipped['width']*scale)),
                 'height':max(1,round(clipped['height']*scale))}
    stamp = source.stat()
    request = dict(schema=2,seconds=seconds,source=str(source),size=stamp.st_size,mtime_ns=stamp.st_mtime_ns,settings=effective)
    identifier = hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()[:32]
    cleanup(cache)
    path = directory(cache,identifier)
    path.mkdir(parents=True,exist_ok=True)
    handle = (path/'.lock').open('a+b')
    if not handle.tell():
        handle.write(b'0');handle.flush()
    if not try_lock(handle):
        handle.close()
        return dict(id=identifier,state='queued',url='/pose-preview/'+identifier)
    existing = status(cache,identifier)
    if existing['state'] == 'ready':
        handle.close()
        return dict(id=identifier,state='ready',url='/pose-preview/'+identifier)
    attempt = path/('attempt-'+uuid.uuid4().hex)
    attempt.mkdir()
    request.update(attempt=attempt.name)
    write(attempt/'request.json',request)
    write(path/'status.json',dict(state='queued',step='รอคิว Preview · งาน Train ที่กำลังรันจะทำต่อจนจบ',progress=0,at=time.time()))
    try:
        threading.Thread(target=_run,args=(path,attempt,queue,owner_alive,handle),daemon=True).start()
    except BaseException:
        handle.close()
        raise
    return dict(id=identifier,state='queued',url='/pose-preview/'+identifier)


def _run(path,attempt,queue,owner_alive,handle):
    try:
        def waiting(ahead):
            write(path/'status.json',dict(state='queued',step=f'รอคิว Preview · มี {ahead} งานก่อนหน้า',progress=0,at=time.time()))
        # The same OS queue lock as normal Views/Train, across web server processes.
        with processing_slot(queue,owner_alive,waiting):
            environment = tool_environment()
            environment.update(STUDIO_WORKSPACE=str(attempt),CPU_THREADS='2',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')
            with (attempt/'worker.log').open('w',encoding='utf8') as log:
                result = subprocess.run(module_command('app.runtime.pose_preview_worker',attempt/'request.json'),
                                        cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,
                                        timeout=300,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode or read(path/'status.json').get('state') != 'ready':
                raise RuntimeError(read(path/'status.json').get('error') or 'คำนวณพรีวิวไม่สำเร็จ กรุณาเลือกช่วงที่กล้องเคลื่อนชัดขึ้น')
    except Exception as exc:
        write(path/'status.json',dict(state='error',error=str(exc),step='พรีวิวไม่สำเร็จ',at=time.time()))
    finally:
        handle.close()
        timer = threading.Timer(TTL_SECONDS+1,cleanup,args=(path.parent,))
        timer.daemon = True
        timer.start()


def image_file(cache, identifier, relative):
    path = directory(cache,identifier)
    record = status(cache,identifier)
    target = (path/relative).resolve()
    if (record['state'] != 'ready' or not target.is_relative_to(path)
            or target.suffix.lower() not in {'.jpg','.jpeg','.png'} or not target.is_file()):
        raise ValueError('ไม่พบภาพพรีวิวหรือพรีวิวหมดอายุ')
    return target
