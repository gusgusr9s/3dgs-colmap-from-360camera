"""Offline Three.js ZIP packages; immutable build keys and a separate CPU queue."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid
import zipfile

from app.models import model_exports as exports
from app.models import model_variants as variants
from app.jobs.processing_queue import processing_slot, try_lock
from app.jobs.task_history import utc_now

from app.paths import ROOT
VENDOR = ROOT/'assets/vendor/three-0.186.1'
REVISION = 1
WINDOWS = '''@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 serve.py
) else (
  python serve.py
)
if errorlevel 1 (
  echo Install Python 3.10 or newer, then try again.
  pause
)
'''.replace('\n', '\r\n').encode('ascii')
UBUNTU = b'#!/usr/bin/env bash\nset -e\ncd -- "$(dirname -- "$0")"\nexec python3 serve.py "$@"\n'


def package_files():
    manifest = json.loads((VENDOR/'offline-manifest.json').read_text(encoding='utf8'))
    files = {
        'index.html': (ROOT/'examples/threejs-splat.html').read_bytes().replace(
            b'https://cdn.jsdelivr.net/npm/three@0.186.1/', b'./vendor/three/'),
        'serve.py': (ROOT/'examples/portable_serve.py').read_bytes(),
        'README.md': (ROOT/'examples/PORTABLE_README.md').read_bytes(),
        'START_WINDOWS.cmd': WINDOWS, 'start_ubuntu.sh': UBUNTU,
        'vendor/three/offline-manifest.json': (VENDOR/'offline-manifest.json').read_bytes(),
    }
    return files, manifest


def build_key(state):
    files, _ = package_files()
    identity = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    identity.update(model_sha256=state.get('sha256'), revision=REVISION)
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]


def package_directory(identifier, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{32}', key):
        raise ValueError('Invalid package key')
    return exports.export_directory(identifier)/'packages'/key


def package_status(identifier, owner_alive, key=None):
    source = exports.export_status(identifier, owner_alive)
    if not source or source['state'] != 'ready':
        return {'state':'unavailable', 'progress':0, 'id':identifier}
    try:
        key = key or build_key(source)
        directory = package_directory(identifier, key)
    except (OSError, ValueError):
        return {'state':'error', 'error':'ไฟล์ตัวอย่างไม่ครบ กรุณาตรวจ assets สำหรับ ZIP', 'id':identifier}
    state = exports._read(directory/'status.json', {'state':'idle', 'progress':0})
    if state['state'] == 'ready':
        target = directory/'bundle.zip'
        if (state.get('model_sha256') != source.get('sha256') or not target.is_file() or
                target.stat().st_size != state.get('bytes')):
            state = {'state':'error', 'error':'ไฟล์ ZIP เปลี่ยนไป กรุณาเตรียมใหม่'}
    if state['state'] in {'queued','running'}:
        active = False
        try:
            with (directory/'.lock').open('r+b') as handle:
                active = not try_lock(handle)
        except FileNotFoundError:
            pass
        if not active or not owner_alive(directory/'status.json'):
            state = {'state':'error', 'error':'การเตรียม ZIP ถูกหยุด กรุณาลองใหม่'}
    return {**state, 'id':identifier, 'key':key}


def start_package(identifier, owner_alive):
    source = exports.export_status(identifier, owner_alive)
    if not source or source['state'] != 'ready':
        raise ValueError('กรุณาเตรียม GLB ให้เสร็จก่อน')
    key = build_key(source)
    directory = package_directory(identifier, key); directory.mkdir(parents=True, exist_ok=True)
    handle = (directory/'.lock').open('a+b')
    if handle.tell() == 0:
        handle.write(b'0'); handle.flush()
    if not try_lock(handle):
        handle.close()
        return package_status(identifier, owner_alive, key)
    try:
        state = package_status(identifier, owner_alive, key)
        if state['state'] == 'ready':
            handle.close(); return state
        state = {'id':identifier, 'key':key, 'state':'queued', 'progress':0,
                 'pid':os.getpid(), 'created_at':utc_now(), 'model_sha256':source['sha256']}
        exports._write(directory, state)
        threading.Thread(target=_package, args=(identifier, state, owner_alive, handle), daemon=True).start()
        return state
    except BaseException:
        handle.close(); raise


def _package(identifier, state, owner_alive, handle):
    directory = package_directory(identifier, state['key'])
    temporary = directory/(uuid.uuid4().hex+'.partial')
    try:
        with processing_slot(variants.VARIANTS/'package_queue', owner_alive), variants._lock:
            source_state = exports.export_status(identifier, owner_alive)
            source = exports.export_directory(identifier)/'model.glb'
            if (not source_state or source_state['state'] != 'ready' or
                    build_key(source_state) != state['key']):
                raise ValueError('Model หรือไฟล์ตัวอย่างเปลี่ยนไป กรุณาเตรียม ZIP ใหม่')
            signature = variants.source_signature(source)
            files, vendor = package_files()
            for name, expected in vendor['files'].items():
                path = (VENDOR/name).resolve()
                if not path.is_relative_to(VENDOR.resolve()):
                    raise ValueError('Invalid vendor path')
                data = path.read_bytes()
                if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                    raise ValueError('Three.js asset checksum mismatch: '+name)
                files['vendor/three/'+name] = data
            state.update(state='running', progress=1); exports._write(directory,state)
            manifest = {'format':'threejs-gaussian-splat-package', 'revision':REVISION,
                        'compression_id':identifier, 'model_sha256':source_state['sha256'], 'files':{}}
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as archive:
                digest = hashlib.sha256(); size = source.stat().st_size; copied = 0; last = 0
                with source.open('rb') as stream, archive.open('model.glb','w',force_zip64=True) as target:
                    while block := stream.read(2*1024*1024):
                        target.write(block); digest.update(block); copied += len(block)
                        progress = min(94, int(copied/max(1,size)*94))
                        if progress != last:
                            state['progress'] = last = progress; exports._write(directory,state)
                if variants.source_signature(source) != signature or digest.hexdigest() != source_state['sha256']:
                    raise ValueError('GLB เปลี่ยนระหว่างเตรียม ZIP กรุณาลองใหม่')
                manifest['files']['model.glb'] = {'bytes':copied, 'sha256':digest.hexdigest()}
                for name,data in files.items():
                    info = zipfile.ZipInfo(name); info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644)<<16
                    info.create_system = 3
                    archive.writestr(info,data,compresslevel=3)
                    manifest['files'][name] = {'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
            sha = variants.sha256_file(temporary)
            temporary.replace(directory/'bundle.zip')
            state.update(state='ready', progress=100, bytes=(directory/'bundle.zip').stat().st_size,
                         sha256=sha, finished_at=utc_now())
            exports._write(directory,state)
    except Exception as exc:
        state.update(state='error',error=str(exc),finished_at=utc_now()); exports._write(directory,state)
    finally:
        temporary.unlink(missing_ok=True); handle.close()
