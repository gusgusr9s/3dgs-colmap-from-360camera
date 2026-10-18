"""On-demand, browser-compatible video copies; source media is never rewritten."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from app.jobs.processing_queue import processing_slot, try_lock


VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv', '.webm'}
_records_lock = threading.RLock()


def preview_directory(source, cache):
    source = Path(source).resolve()
    stat = source.stat()
    signature = ('h264-preview-v1', str(source), stat.st_size, stat.st_mtime_ns)
    return Path(cache) / hashlib.sha256(repr(signature).encode()).hexdigest()


def _write(directory, record):
    temporary = directory / (uuid.uuid4().hex + '.tmp')
    try:
        with _records_lock:
            temporary.write_text(json.dumps(record), encoding='utf8')
            # Windows can briefly deny replacement while another process reads.
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


def preview_status(source, cache, owner_alive):
    directory = preview_directory(source, cache)
    try:
        with _records_lock:
            record = json.loads((directory / 'status.json').read_text(encoding='utf8'))
    except (OSError, ValueError):
        record = {'state': 'idle', 'progress': 0}
    if record.get('state') == 'ready' and not (directory / 'preview.mp4').is_file():
        record = {'state': 'error', 'error': 'ไม่พบวิดีโอ Preview กรุณาลองอีกครั้ง'}
    if record.get('state') in {'queued', 'running'}:
        # A server may still be alive after its worker has failed.
        active = False
        try:
            with (directory / '.lock').open('r+b') as handle:
                active = not try_lock(handle)
        except FileNotFoundError:
            pass
        if not active or not owner_alive(directory / 'status.json'):
            record = {'state': 'error', 'error': 'การเตรียม Preview ถูกหยุด กรุณาลองอีกครั้ง'}
    return {**record, 'id': directory.name}


def start_preview(source, cache, owner_alive):
    source, cache = Path(source), Path(cache)
    if source.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError('ไฟล์นี้ไม่ใช่วิดีโอที่รองรับ')
    directory = preview_directory(source, cache)
    directory.mkdir(parents=True, exist_ok=True)
    # Keep the OS lock until the worker finishes, also across server processes.
    handle = (directory / '.lock').open('a+b')
    if handle.tell() == 0:
        handle.write(b'0'); handle.flush()
    if not try_lock(handle):
        handle.close()
        return preview_status(source, cache, owner_alive)
    record = preview_status(source, cache, owner_alive)
    if record['state'] == 'ready':
        handle.close()
        return record
    record = {'state': 'queued', 'progress': 0, 'pid': os.getpid()}
    try:
        _write(directory, record)
        worker = threading.Thread(target=_convert, args=(source, cache, directory, owner_alive, handle), daemon=True)
        worker.start()
    except BaseException:
        handle.close()
        raise
    return {**record, 'id': directory.name}


def _convert(source, cache, directory, owner_alive, handle):
    temporary = directory / 'preview.partial.mp4'
    process = None
    record = {'state': 'queued', 'progress': 0, 'pid': os.getpid()}
    try:
        # A separate CPU queue: never competes for GPU memory with Views or Train.
        with processing_slot(cache / 'queue', owner_alive):
            from app.runtime.tool_environment import find_tool
            ffmpeg, ffprobe = find_tool('ffmpeg'), find_tool('ffprobe')
            if not ffmpeg or not ffprobe:
                raise ValueError('ต้องติดตั้ง FFmpeg และ ffprobe เพื่อเตรียมวิดีโอ Preview')
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            metadata = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                                       '-of', 'json', str(source)], capture_output=True, text=True,
                                      timeout=30, check=True, **options)
            duration = float(json.loads(metadata.stdout)['format']['duration'])
            command = [ffmpeg, '-nostdin', '-y', '-threads', '2', '-filter_threads', '2', '-i', str(source),
                       '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
                       '-vf', "scale=w='trunc(min(1920,iw)/2)*2':h=-2,fps=30",
                       '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-pix_fmt', 'yuv420p',
                       '-threads', '2', '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-ar', '48000',
                       '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', str(temporary)]
            record.update(state='running')
            _write(directory, record)
            with (directory / 'ffmpeg.log').open('w', encoding='utf8') as errors:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True,
                                           encoding='utf8', errors='replace', **options)
                updated = 0
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        try:
                            progress = max(0, min(99, float(line.split('=', 1)[1]) / 1e6 / duration * 100))
                        except (ValueError, ZeroDivisionError):
                            continue
                        if time.monotonic() - updated >= .5:
                            record['progress'] = round(progress, 1)
                            _write(directory, record)
                            updated = time.monotonic()
                if process.wait() or not temporary.is_file() or not temporary.stat().st_size:
                    raise ValueError('แปลงวิดีโอไม่สำเร็จ กรุณาลองอีกครั้ง (รายละเอียดอยู่ใน ffmpeg.log)')
            if preview_directory(source, cache) != directory:
                raise ValueError('ต้นฉบับเปลี่ยนระหว่างเตรียม Preview กรุณาลองอีกครั้ง')
            temporary.replace(directory / 'preview.mp4')
            _write(directory, {**record, 'state': 'ready', 'progress': 100})
    except Exception as exc:
        _write(directory, {**record, 'state': 'error', 'error': str(exc) if isinstance(exc, ValueError)
                          else 'เตรียมวิดีโอ Preview ไม่สำเร็จ กรุณาตรวจ FFmpeg แล้วลองอีกครั้ง'})
    finally:
        if process:
            if process.poll() is None:
                process.kill(); process.wait()
            process.stdout.close()
        try:
            temporary.unlink(missing_ok=True)
        finally:
            handle.close()
