"""Bounded JPEG preparation with a content-addressed, validated pixel cache.

The cache retains the existing OpenCV decode/JPEG-95 recipe exactly. Reusing
encoded bytes avoids another lossy encode, rather than changing training pixels.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import shutil
import time
import threading
import uuid

import cv2
import numpy as np

from app.runtime.cpu_runtime import cpu_budget
from app.jobs.task_control import read, write

RECIPE = hashlib.sha256(('opencv-jpeg95:'+cv2.__version__+cv2.getBuildInformation()).encode()).hexdigest()
_cache_locks=[threading.Lock() for _ in range(64)]


def stamp(path):
    value=Path(path).stat()
    return [value.st_size,value.st_mtime_ns]


def atomic_link(source,target):
    target=Path(target)
    temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        try:os.link(source,temporary)
        except OSError:shutil.copy2(source,temporary)
        temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)


def normalize(source,target,cache):
    source,target,cache=Path(source),Path(target),Path(cache)
    before=stamp(source)
    pixels=source.read_bytes()
    if stamp(source)!=before:
        raise ValueError('Source image changed during dataset preparation: '+source.name)
    source_hash=hashlib.sha256(pixels).hexdigest()
    key=hashlib.sha256((RECIPE+source_hash).encode()).hexdigest()
    with _cache_locks[int(key[:2],16)%len(_cache_locks)]:
        return _normalize_cached(source,target,cache,before,pixels,source_hash,key)


def _normalize_cached(source,target,cache,before,pixels,source_hash,key):
    encoded_path=cache/(key+'.jpg');receipt_path=cache/(key+'.json')
    receipt=read(receipt_path)
    hit=False
    if receipt.get('source_sha256')==source_hash and encoded_path.is_file():
        encoded=encoded_path.read_bytes()
        hit=len(encoded)==receipt.get('bytes') and hashlib.sha256(encoded).hexdigest()==receipt.get('sha256')
    if not hit:
        decoded=cv2.imdecode(np.frombuffer(pixels,dtype=np.uint8),cv2.IMREAD_COLOR)
        if decoded is None:raise ValueError('Could not decode perspective image: '+source.name)
        success,encoded=cv2.imencode('.jpg',decoded,[cv2.IMWRITE_JPEG_QUALITY,95])
        if not success:raise ValueError('Could not normalize perspective image: '+source.name)
        encoded=encoded.tobytes()
        temporary=encoded_path.with_name(key+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_bytes(encoded)
            for attempt in range(10):
                try:temporary.replace(encoded_path);break
                except PermissionError:
                    if attempt==9:raise
                    time.sleep(.02)
        finally:temporary.unlink(missing_ok=True)
        write(receipt_path,{'recipe':RECIPE,'source_sha256':source_hash,'bytes':len(encoded),
                            'sha256':hashlib.sha256(encoded).hexdigest()})
    atomic_link(encoded_path,target)
    return {'source':source.name,'source_stamp':before,'source_sha256':source_hash,
            'output':target.name,'output_stamp':stamp(target),'sha256':hashlib.sha256(encoded).hexdigest(),
            'cache_hit':hit}


def worker_count(width,height,environment=None):
    environment=os.environ if environment is None else environment
    requested=environment.get('DATASET_WORKERS','').strip()
    if requested and (not requested.isdigit() or int(requested)<1):
        raise ValueError('DATASET_WORKERS must be a positive integer')
    # Bound both concurrency and pending decoded/encoded images (roughly 256 MB).
    memory_limit=max(1,(256*1024**2)//max(1,int(width)*int(height)*12))
    return max(1,min(cpu_budget(environment).threads,int(requested) if requested else 8,memory_limit))


def prepare_images(pairs,cache,width,height,progress=None):
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    workers=worker_count(width,height)
    records=[];pending=deque();iterator=iter(pairs)
    tick=time.monotonic();last=tick
    if progress:progress(0,len(pairs))
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='dataset-jpeg') as pool:
        def fill():
            for source,target in iterator:
                pending.append(pool.submit(normalize,source,target,cache))
                if len(pending)>=workers*2:break
        try:
            fill()
            while pending:
                records.append(pending.popleft().result())
                now=time.monotonic()
                if progress and (len(records)==len(pairs) or now-last>=2):
                    progress(len(records),len(pairs));last=now
                fill()
        except BaseException:
            for future in pending:future.cancel()
            raise
    return records,{'workers':workers,'max_pending':workers*2,'images':len(records),
                    'cache_hits':sum(row['cache_hit'] for row in records),
                    'encoded':sum(not row['cache_hit'] for row in records),
                    'elapsed_seconds':round(time.monotonic()-tick,3),'recipe':'OpenCV decode + JPEG quality 95 (unchanged)'}


def valid_manifest(view_dir,output_dir,metadata):
    """Cheap reuse guard; source/output stamps catch changed or missing inputs."""
    output_dir=Path(output_dir);view_dir=Path(view_dir)
    manifest=read(output_dir/'dataset_complete.json')
    if not manifest:return False
    from app.pipeline.reconstruction_preview import pose_input_signature
    try:
        if manifest.get('input_signature')!=pose_input_signature(view_dir):return False
        if manifest.get('transforms_sha256')!=hashlib.sha256((output_dir/'transforms.json').read_bytes()).hexdigest():return False
        rows=manifest['images']
        if len(rows)!=len(metadata.get('frames',[])):return False
        for row in rows:
            source=(view_dir/row['source']).resolve();target=(output_dir/'images'/row['output']).resolve()
            if source.parent!=view_dir.resolve() or target.parent!=(output_dir/'images').resolve():return False
            if stamp(source)!=row['source_stamp'] or stamp(target)!=row['output_stamp']:return False
        seed=(output_dir/metadata.get('ply_file_path','sparse_pc.ply')).resolve()
        return seed.is_relative_to(output_dir.resolve()) and stamp(seed)==manifest['seed_stamp']
    except (OSError,KeyError,TypeError,ValueError):return False
