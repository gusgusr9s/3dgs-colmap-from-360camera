"""Background scene GLB exports. OBJ continues to represent authored collision boxes."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

from app.jobs.processing_queue import processing_slot, try_lock
from app.models.scene_package import _read, _write
from app.models.scene_glb import Composer, glb_info, ply_to_glb
from app.paths import ROOT, WORKSPACE

EXPORTS=WORKSPACE/'web_data'/'scene_exports'
_guard=threading.RLock()
_active=set()
REVISION=1


def directory(identifier):
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('Invalid scene ID')
    root=Path(EXPORTS).resolve();folder=root/identifier
    if folder.resolve().parent!=root:raise ValueError('Invalid export path')
    return folder


def download_path(identifier,key,check_status=True):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Invalid export key')
    folder=directory(identifier)
    if check_status:
        state=_read(folder)
        if state.get('state')!='ready' or state.get('key')!=key:raise ValueError('GLB is not ready')
    target=folder/(key+'.glb')
    if target.resolve().parent!=folder.resolve() or not target.is_file():raise ValueError('GLB not found')
    return target


def status(identifier):
    folder=directory(identifier)
    with _guard:
        state=_read(folder)
        if state['state'] in {'queued','running'} and identifier not in _active:
            try:
                with (folder/'.lock').open('a+b') as lock:
                    if try_lock(lock):state.update(state='error',error='การส่งออก GLB ถูกหยุด กรุณาลองอีกครั้ง');_write(folder,state)
            except FileNotFoundError:state={'state':'idle','progress':0}
        if state['state']=='ready':
            try:
                if download_path(identifier,state['key'],False).stat().st_size!=state['bytes']:raise ValueError('GLB changed')
            except (OSError,ValueError):state={'state':'error','progress':0,'error':'ไฟล์ GLB ไม่ครบ กรุณาส่งออกใหม่'}
        return state


def start(scene,resolver,owner_alive):
    scene=json.loads(json.dumps(scene));sources={}
    for item in scene['objects']:
        if not item['visible']:continue
        asset=item['asset'];key=(asset['kind'],asset['ref'])
        path=Path(resolver(*key)).resolve();stat=path.stat();sources[key]=(path,[stat.st_size,stat.st_mtime_ns])
    if not sources:raise ValueError('ต้องมีโมเดลที่แสดงอยู่ในฉากก่อนส่งออก GLB')
    code=[ROOT/'app/models/scene_glb.py',ROOT/'scripts/export_splat_glb.mjs']
    fingerprint={'exporter':REVISION,'scene':scene,'sources':{kind+':'+ref:signature for (kind,ref),(_,signature) in sources.items()},'code':[hashlib.sha256(path.read_bytes()).hexdigest() for path in code]}
    key=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest()[:32]
    folder=directory(scene['id']);folder.mkdir(parents=True,exist_ok=True)
    with _guard:
        if scene['id'] in _active:return _read(folder)
        handle=(folder/'.lock').open('a+b')
        if handle.seek(0,2)==0:handle.write(b'0');handle.flush()
        if not try_lock(handle):handle.close();return _read(folder)
        old=status(scene['id'])
        if old.get('state')=='ready' and old.get('key')==key:handle.close();return old
        record={'state':'queued','progress':0,'key':key,'scene_id':scene['id'],'revision':scene['revision'],'pid':os.getpid()}
        _write(folder,record);_active.add(scene['id'])
        threading.Thread(target=_build,args=(scene,sources,folder,record,handle,owner_alive),daemon=True).start()
        return record


def _build(scene,sources,folder,record,handle,owner_alive):
    target=folder/(record['key']+'.glb');temporary=folder/(record['key']+'.partial')
    def progress(value):
        value=int(value)
        if value!=record.get('progress'):record.update(progress=value);_write(folder,record)
    try:
        with processing_slot(Path(EXPORTS)/'.queue',owner_alive), tempfile.TemporaryDirectory(prefix='convert_',dir=folder) as working:
            record.update(state='running',progress=1);_write(folder,record);converted={};splat_counts={}
            for index,((kind,ref),(path,signature)) in enumerate(sources.items()):
                output=Path(working)/(str(index)+'.glb')
                update=lambda value:progress(1+int(69*(index+value/100)/len(sources)))
                if kind=='glb':output=path
                elif kind=='ply':splat_counts[(kind,ref)]=ply_to_glb(path,output,update)['splats']
                elif kind=='spz':
                    node=shutil.which('node')
                    if not node:raise ValueError('ต้องติดตั้ง Node.js เพื่อส่งออก SPZ เป็น GLB')
                    progress_file=Path(working)/(str(index)+'.json')
                    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
                    process=subprocess.Popen([node,str(ROOT/'scripts/export_splat_glb.mjs'),str(path),str(output),'',str(progress_file)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf8',**options)
                    try:
                        deadline=time.monotonic()+1800
                        while process.poll() is None:
                            if time.monotonic()>deadline:raise TimeoutError('ส่งออกโมเดลเกินเวลา กรุณาลดขนาดฉาก')
                            try:update(json.loads(progress_file.read_text(encoding='utf8')).get('progress',0))
                            except (OSError,ValueError):pass
                            time.sleep(.2)
                        stdout,stderr=process.communicate()
                        if process.returncode:raise ValueError(stderr[-2000:] or 'SPZ GLB export failed')
                        splat_counts[(kind,ref)]=json.loads(stdout.strip().splitlines()[-1])['splats']
                    finally:
                        if process.poll() is None:process.kill();process.communicate()
                else:raise ValueError('Unsupported scene asset')
                converted[(kind,ref)]=output;update(100)
            composer=Composer(scene)
            for item in scene['objects']:
                if item['visible']:composer.add(converted[(item['asset']['kind'],item['asset']['ref'])],item)
            composer.collision();result=composer.write(temporary,lambda value:progress(70+int(.28*value)))
            glb_info(temporary)
            for path,signature in sources.values():
                stat=path.stat()
                if [stat.st_size,stat.st_mtime_ns]!=signature:raise ValueError('โมเดลต้นทางเปลี่ยนระหว่างส่งออก กรุณาลองใหม่')
            digest=hashlib.sha256()
            with temporary.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            splat_count=sum(splat_counts.get((item['asset']['kind'],item['asset']['ref']),0) for item in scene['objects'] if item['visible'])
            temporary.replace(target);record.update(result,state='ready',progress=100,splats=splat_count,sha256=digest.hexdigest());_write(folder,record)
    except Exception as error:
        record.update(state='error',error=str(error));_write(folder,record)
    finally:
        temporary.unlink(missing_ok=True)
        with _guard:handle.close();_active.discard(scene['id'])
