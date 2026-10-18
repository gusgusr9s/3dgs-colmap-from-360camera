"""Bounded JPEG preparation: link proven-safe Views JPEGs, otherwise re-encode once.

Brush's "early eof" was never a JPEG decoder problem: the message comes from
tokio's read_exact. Brush 0.3 reads a fixed-size prefix of every training image,
so any image file under 16,387 bytes fails, JPEG or PNG alike (bisected with
tools/brush on 2026-10-18; the 192x192 Tram views that failed were that small).
A Views JPEG that is a complete baseline JPEG of the expected size and is large
enough is therefore linked unchanged: identical pixels, one JPEG generation less.
Anything not proven safe keeps the OpenCV decode/JPEG-95 recipe and its
content-addressed cache; outputs below the size limit get zero padding after EOI
(pixels unchanged).
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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

RECIPE = hashlib.sha256(('opencv-jpeg95+brush-pad2:'+cv2.__version__+cv2.getBuildInformation()).encode()).hexdigest()
BRUSH_MIN_BYTES = 16387  # Smallest image file Brush 0.3 loads; measured by bisection.
SAFE_BYTES = 32768  # Margin for linking and padding target.
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


def jpeg_frame(data):
    """(width, height, components) of a complete single-scan 8-bit baseline JPEG, else None.

    Checks the marker structure and that the one entropy-coded scan runs, with only
    stuffed bytes and restart markers, up to an EOI at the very end. No pixel decode."""
    if len(data)<4 or data[:2]!=b'\xff\xd8' or data[-2:]!=b'\xff\xd9':return None
    index=2;frame=None
    while index+4<=len(data):
        if data[index]!=0xFF:return None
        marker=data[index+1]
        if marker==0xFF:index+=1;continue
        length=int.from_bytes(data[index+2:index+4],'big')
        if length<2 or index+2+length>len(data):return None
        if marker==0xC0:
            segment=data[index+4:index+2+length]
            if frame or len(segment)<6 or segment[0]!=8:return None
            frame=int.from_bytes(segment[3:5],'big'),int.from_bytes(segment[1:3],'big'),segment[5]
        elif marker==0xDA:
            scan=np.frombuffer(data,dtype=np.uint8)[index+2+length:-2]
            if frame is None or not len(scan) or scan[-1]==0xFF:return None
            following=scan[np.flatnonzero(scan[:-1]==0xFF)+1]
            return frame if bool(np.all((following==0)|((following>=0xD0)&(following<=0xD7)))) else None
        # Progressive/lossless/arithmetic frames or EOI before a scan: not proven.
        elif marker in (0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF,0xD9):return None
        index+=2+length
    return None


def pad_for_brush(data):
    """Append zero bytes after EOI so the file meets Brush's minimum size; decoders stop at EOI.

    A COM segment does not work: Brush then fails the same way, apparently because
    the frame header must lie inside the prefix it reads (measured)."""
    return data if len(data)>=SAFE_BYTES else data+bytes(SAFE_BYTES-len(data))


def link_proof(pixels,source_hash,name,width,height,trusted):
    """Why this file can go to Brush unchanged, or None to re-encode it."""
    if width is None or height is None or len(pixels)<SAFE_BYTES or jpeg_frame(pixels)!=(int(width),int(height),3):
        return None
    entry=(trusted or {}).get(name) or {}
    if entry.get('sha256')==source_hash and entry.get('bytes')==len(pixels):
        return 'views-manifest'
    decoded=cv2.imdecode(np.frombuffer(pixels,dtype=np.uint8),cv2.IMREAD_COLOR)
    return 'decoded' if decoded is not None and decoded.shape==(int(height),int(width),3) else None


def normalize(source,target,cache,width=None,height=None,trusted=None):
    source,target,cache=Path(source),Path(target),Path(cache)
    before=stamp(source)
    pixels=source.read_bytes()
    if stamp(source)!=before:
        raise ValueError('Source image changed during dataset preparation: '+source.name)
    source_hash=hashlib.sha256(pixels).hexdigest()
    proof=link_proof(pixels,source_hash,source.name,width,height,trusted)
    if proof:
        atomic_link(source,target)
        if stamp(target)!=before:
            raise ValueError('Source image changed during dataset preparation: '+source.name)
        return {'source':source.name,'source_stamp':before,'source_sha256':source_hash,
                'output':target.name,'output_stamp':stamp(target),'sha256':source_hash,
                'cache_hit':False,'linked':proof}
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
        encoded=pad_for_brush(encoded.tobytes())
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
            'cache_hit':hit,'linked':None}


def worker_count(width,height,environment=None):
    environment=os.environ if environment is None else environment
    requested=environment.get('DATASET_WORKERS','').strip()
    if requested and (not requested.isdigit() or int(requested)<1):
        raise ValueError('DATASET_WORKERS must be a positive integer')
    # Bound both concurrency and pending decoded/encoded images (roughly 256 MB).
    memory_limit=max(1,(256*1024**2)//max(1,int(width)*int(height)*12))
    return max(1,min(cpu_budget(environment).threads,int(requested) if requested else 8,memory_limit))


def prepare_images(pairs,cache,width,height,progress=None,trusted=None):
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    workers=worker_count(width,height)
    results={};pending={};iterator=enumerate(pairs)
    tick=time.monotonic();last=tick
    if progress:progress(0,len(pairs))
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='dataset-jpeg') as pool:
        def fill():
            for index,(source,target) in iterator:
                pending[pool.submit(normalize,source,target,cache,width,height,trusted)]=index
                if len(pending)>=workers*2:break
        try:
            fill()
            while pending:
                # Collect in completion order: one slow file (e.g. an antivirus scan) must not idle the pool.
                finished,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in finished:results[pending.pop(future)]=future.result()
                now=time.monotonic()
                if progress and (len(results)==len(pairs) or now-last>=2):
                    progress(len(results),len(pairs));last=now
                fill()
        except BaseException:
            for future in pending:future.cancel()
            raise
    records=[results[index] for index in range(len(results))]
    linked=sum(bool(row['linked']) for row in records)
    return records,{'workers':workers,'max_pending':workers*2,'images':len(records),
                    'linked':linked,'linked_by_views_manifest':sum(row['linked']=='views-manifest' for row in records),
                    'cache_hits':sum(row['cache_hit'] for row in records),
                    'encoded':sum(not row['cache_hit'] and not row['linked'] for row in records),
                    'elapsed_seconds':round(time.monotonic()-tick,3),
                    'recipe':'Views JPEG linked unchanged when proven safe for Brush; otherwise OpenCV decode + JPEG quality 95'}


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
