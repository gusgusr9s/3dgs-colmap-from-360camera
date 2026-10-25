"""Stream self-contained scene ZIPs on a background CPU worker."""
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile

from app.jobs.processing_queue import try_lock
from app.models import scene_projects
from app.paths import ROOT, WORKSPACE

PACKAGES = WORKSPACE / 'web_data' / 'scene_packages'
_guard = threading.RLock()
_active = set()
REVISION = 1
APP_FILES = ['scene_player.mjs','scene_geometry.mjs','scene_collision.mjs']


def directory(identifier):
    if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{32}',identifier):
        raise ValueError('Invalid scene ID')
    root=Path(PACKAGES).resolve();target=root/identifier
    if target.resolve().parent!=root:
        raise ValueError('Invalid package path')
    return target


def _write(folder, record):
    temporary=folder/(uuid.uuid4().hex+'.tmp')
    try:
        with _guard:
            temporary.write_text(json.dumps(record,ensure_ascii=False),encoding='utf8')
            for attempt in range(40):
                try:
                    temporary.replace(folder/'status.json')
                    break
                except PermissionError:
                    if attempt == 39:raise
                    time.sleep(.025)
    finally:
        temporary.unlink(missing_ok=True)


def _read(folder):
    try:
        with _guard:
            return json.loads((folder/'status.json').read_text(encoding='utf8'))
    except (OSError,ValueError):
        return {'state':'idle','progress':0}


def status(identifier):
    folder=directory(identifier)
    with _guard:
        state=_read(folder)
        if state['state'] in {'queued','running'} and identifier not in _active:
            try:
                with (folder/'.lock').open('a+b') as lock:
                    if try_lock(lock):
                        state.update(state='error',error='การเตรียม ZIP ถูกหยุด กรุณาลองอีกครั้ง')
                        _write(folder,state)
            except FileNotFoundError:
                state={'state':'idle','progress':0}
        if state['state']=='ready':
            try:
                target=download_path(identifier,state['key'],check_status=False)
                if target.stat().st_size!=state['bytes']:
                    raise ValueError('ZIP changed')
            except (OSError,ValueError):
                state={'state':'error','error':'ไฟล์ ZIP ไม่ครบ กรุณาเตรียมใหม่','progress':0}
        return state


def download_path(identifier,key,check_status=True):
    if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{32}',key):
        raise ValueError('Invalid package key')
    folder=directory(identifier)
    if check_status:
        state=_read(folder)
        if state.get('state')!='ready' or state.get('key')!=key:
            raise ValueError('ZIP is not ready')
    target=folder/(key+'.zip')
    if target.resolve().parent!=folder.resolve() or not target.is_file():
        raise ValueError('ZIP not found')
    return target


def start(scene,resolver):
    scene=json.loads(json.dumps(scene))
    files={};catalog=[];unique={}
    for item in scene['objects']:
        asset=item['asset'];key=asset['kind']+':'+asset['ref']
        if key in unique:
            continue
        path=Path(resolver(asset['kind'],asset['ref']))
        signature=[path.stat().st_size,path.stat().st_mtime_ns]
        name=f'models/model-{len(unique)}.{asset["kind"]}'
        unique[key]=[name,*signature]
        files[name]=(path,signature)
        catalog.append({**asset,'label':item['name'],'url':'./'+name,'bytes':signature[0]})
    vendor=json.loads((ROOT/'assets/vendor/manifest.json').read_text(encoding='utf8'))
    for name,info in vendor.items():
        source=ROOT/'assets/vendor'/name
        if not source.resolve().is_relative_to((ROOT/'assets/vendor').resolve()):
            raise ValueError('Invalid vendor path')
        data=source.read_bytes()
        if hashlib.sha256(data).hexdigest()!=info['sha256']:
            raise ValueError('Viewer dependency checksum mismatch: '+name)
        files['vendor/'+name]=(source,[len(data),source.stat().st_mtime_ns])
    for name in APP_FILES:
        source=ROOT/'assets/app'/name;files['assets/'+name]=(source,[source.stat().st_size,source.stat().st_mtime_ns])
    html=(ROOT/'app/web/templates/scene_player.html').read_bytes()
    fingerprint={'revision':REVISION,'scene':scene,'sources':unique,'code':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,(path,_) in files.items() if not name.startswith('models/')},'html':hashlib.sha256(html).hexdigest()}
    key=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest()[:32]
    folder=directory(scene['id']);folder.mkdir(parents=True,exist_ok=True)
    with _guard:
        if scene['id'] in _active:
            return _read(folder)
        lock=(folder/'.lock').open('a+b')
        if lock.seek(0,2)==0:lock.write(b'0');lock.flush()
        if not try_lock(lock):
            lock.close();return _read(folder)
        old=_read(folder)
        if old.get('key')==key and old.get('state')=='ready' and (folder/(key+'.zip')).is_file():
            lock.close();return old
        record={'state':'queued','progress':0,'key':key,'scene_id':scene['id'],'revision':scene['revision']}
        _write(folder,record)
        _active.add(scene['id'])
        threading.Thread(target=_build,args=(scene,catalog,files,html,folder,record,lock),daemon=True).start()
        return record


def _build(scene,catalog,files,html,folder,record,lock):
    temporary=folder/(record['key']+'.partial')
    try:
        record.update(state='running',progress=1);_write(folder,record)
        manifest={'scene_id':scene['id'],'revision':scene['revision'],'files':{}}
        total=sum(signature[0] for _,signature in files.values()) or 1;copied=0
        with zipfile.ZipFile(temporary,'w',allowZip64=True) as archive:
            def add_bytes(name,data):
                archive.writestr(name,data,compress_type=zipfile.ZIP_DEFLATED)
                manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            add_bytes('index.html',html)
            add_bytes('scene.json',json.dumps({'scene':scene,'assets':catalog},ensure_ascii=False).encode('utf8'))
            for name,(source,signature) in files.items():
                digest=hashlib.sha256();count=0
                info=zipfile.ZipInfo(name);info.compress_type=zipfile.ZIP_STORED if name.startswith('models/') else zipfile.ZIP_DEFLATED
                with source.open('rb') as stream,archive.open(info,'w',force_zip64=True) as output:
                    while True:
                        data=stream.read(1024*1024)
                        if not data:break
                        output.write(data);digest.update(data);count+=len(data);copied+=len(data)
                        record['progress']=min(98,1+int(96*copied/total));_write(folder,record)
                if count!=signature[0] or [source.stat().st_size,source.stat().st_mtime_ns]!=signature:
                    raise ValueError('ไฟล์ต้นทางเปลี่ยนระหว่างเตรียม ZIP กรุณาลองอีกครั้ง')
                manifest['files'][name]={'bytes':count,'sha256':digest.hexdigest()}
            serve='''from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os, webbrowser
os.chdir(Path(__file__).resolve().parent)
try: server=ThreadingHTTPServer(('127.0.0.1',8080),SimpleHTTPRequestHandler)
except OSError: server=ThreadingHTTPServer(('127.0.0.1',0),SimpleHTTPRequestHandler)
url='http://127.0.0.1:'+str(server.server_port)
print(url,flush=True)
webbrowser.open(url)
server.serve_forever()
'''
            add_bytes('serve.py',serve.encode())
            add_bytes('START_WINDOWS.cmd',b'@echo off\r\ncd /d "%~dp0"\r\npython serve.py\r\npause\r\n')
            add_bytes('start_ubuntu.sh',b'#!/usr/bin/env bash\ncd -- "$(dirname -- "$0")"\npython3 serve.py\n')
            add_bytes('README.txt',('360 Studio portable scene\nExtract the entire ZIP. Python 3.10+ is required to run serve.py.\nWindows: START_WINDOWS.cmd\nLinux: bash start_ubuntu.sh\nOr host this directory over HTTP/HTTPS. No internet or original server is needed.\nModels, authored collision boxes and scene transforms are included.\nUse Orbit or Walk. Collisions apply only to authored boxes.\nThird-party licenses are included under vendor/.\n').encode())
            archive.writestr('manifest.json',json.dumps(manifest,indent=2))
        target=folder/(record['key']+'.zip');temporary.replace(target)
        record.update(state='ready',progress=100,bytes=target.stat().st_size);_write(folder,record)
    except Exception as exc:
        record.update(state='error',error=str(exc));_write(folder,record)
    finally:
        temporary.unlink(missing_ok=True)
        with _guard:
            lock.close();_active.discard(scene['id'])
