"""Cached Gaussian GLB exports from immutable SPZ records, on a separate CPU queue."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import threading
import time
import uuid

import model_variants as variants
from processing_queue import processing_slot, try_lock
from task_history import utc_now

ROOT = Path(__file__).resolve().parent
_records_lock = threading.RLock()


def export_directory(identifier):
    return variants.compression_directory(identifier) / 'glb_v1'


def _read(path, default):
    try:
        value = json.loads(path.read_text(encoding='utf8'))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def _write(directory, record):
    with _records_lock:
        temporary = directory / (uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf8')
            for attempt in range(40):
                try:
                    temporary.replace(directory / 'status.json')
                    break
                except PermissionError:
                    if attempt == 39:
                        raise
                    time.sleep(.025)
        finally:
            temporary.unlink(missing_ok=True)


def export_status(identifier, owner_alive):
    record = variants.compression_record(identifier)
    if not record or record.get('state') != 'ready':
        return None
    directory = export_directory(identifier)
    source = directory.parent / 'model.spz'
    if not source.is_file():
        return None
    with _records_lock:
        state = _read(directory / 'status.json', {'state': 'idle', 'progress': 0})
    if state.get('state') == 'ready':
        target = directory / 'model.glb'
        if (state.get('exporter_revision') != 2 or state.get('source_sha256') != record.get('sha256') or
                state.get('source_signature') != variants.source_signature(source) or
                not target.is_file() or target.stat().st_size != state.get('bytes')):
            state = {'state': 'error', 'error': 'ไฟล์ส่งออกหรือต้นทางเปลี่ยนไป กรุณาส่งออกใหม่'}
    if state.get('state') in {'queued', 'running'}:
        active = False
        try:
            with (directory / '.lock').open('r+b') as handle:
                active = not try_lock(handle)
        except FileNotFoundError:
            pass
        if not active or not owner_alive(directory / 'status.json'):
            state = {'state': 'error', 'error': 'การส่งออกถูกหยุด กรุณาลองอีกครั้ง'}
        elif state['state'] == 'running':
            value = _read(directory / 'progress.json', {}).get('progress', 0)
            if isinstance(value, (int, float)):
                state['progress'] = max(0, min(99, value))
    return {**state, 'id': identifier}


def start_export(identifier, owner_alive):
    if export_status(identifier, owner_alive) is None:
        raise ValueError('ไม่พบ SPZ ที่พร้อมส่งออก')
    directory = export_directory(identifier)
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / '.lock').open('a+b')
    if handle.tell() == 0:
        handle.write(b'0'); handle.flush()
    if not try_lock(handle):
        handle.close()
        return export_status(identifier, owner_alive)
    try:
        status = export_status(identifier, owner_alive)
        if status and status['state'] == 'ready':
            handle.close()
            return status
        record = {'id': identifier, 'state': 'queued', 'progress': 0, 'pid': os.getpid(), 'created_at': utc_now()}
        _write(directory, record)
        threading.Thread(target=_convert, args=(identifier, record, owner_alive, handle), daemon=True).start()
        return record
    except BaseException:
        handle.close()
        raise


def _convert(identifier, state, owner_alive, handle):
    directory = export_directory(identifier)
    temporary = directory / (uuid.uuid4().hex + '.partial.glb')
    started = time.monotonic()
    try:
        # Queue across servers; also avoid overlapping the memory-heavy SPZ encoder in this process.
        with processing_slot(variants.VARIANTS / 'glb_queue', owner_alive), variants._lock:
            source = directory.parent / 'model.spz'
            record = variants.compression_record(identifier)
            if not record or record.get('state') != 'ready':
                raise ValueError('ไม่พบ SPZ ที่พร้อมส่งออก')
            signature = variants.source_signature(source)
            digest = variants.sha256_file(source)
            if digest != record.get('sha256'):
                raise ValueError('SPZ ไม่ตรงกับ record เดิม กรุณาตรวจไฟล์ต้นทาง')
            header = variants.spz_header(source)
            node = shutil.which('node')
            if not node:
                raise ValueError('ต้องติดตั้ง Node.js เพื่อส่งออก GLB')
            (directory / 'progress.json').unlink(missing_ok=True)
            state.update(state='running', progress=0, source_sha256=digest, source_signature=signature)
            _write(directory, state)
            metadata = {'compression_id': identifier, 'source_sha256': digest,
                        'initial_view': record.get('parent_info', {}).get('viewer_config', {}),
                        'units': 'reconstruction units; not calibrated meters'}
            (directory / 'metadata.json').write_text(json.dumps(metadata), encoding='utf8')
            command = [node, str(ROOT / 'scripts/export_splat_glb.mjs'), str(source), str(temporary),
                       str(directory / 'metadata.json'), str(directory / 'progress.json')]
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf8', timeout=1800, **options)
            if result.returncode:
                raise RuntimeError(result.stderr[-2000:] or 'GLB export failed')
            output = json.loads(result.stdout.strip().splitlines()[-1])
            with temporary.open('rb') as stream:
                magic, version, length = struct.unpack('<III', stream.read(12))
            if (magic != 0x46546c67 or version != 2 or length != temporary.stat().st_size or
                    length != output['bytes'] or output['splats'] != header['splats'] or output['sh_degree'] != header['sh_degree']):
                raise ValueError('ผลส่งออก GLB ไม่ผ่านการตรวจจำนวนจุด / SH / ขนาดไฟล์')
            if variants.source_signature(source) != signature or variants.sha256_file(source) != digest:
                raise ValueError('SPZ เปลี่ยนระหว่างส่งออก กรุณาลองอีกครั้ง')
            output['sha256'] = variants.sha256_file(temporary)
            temporary.replace(directory / 'model.glb')
            state.update(output, state='ready', progress=100, exporter_revision=2, finished_at=utc_now(), duration_seconds=time.monotonic()-started)
            _write(directory, state)
    except Exception as exc:
        state.update(state='error', error=str(exc), finished_at=utc_now())
        _write(directory, state)
    finally:
        temporary.unlink(missing_ok=True)
        handle.close()
