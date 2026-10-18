"""Read-only, bounded log snapshots for the progress console, including legacy runs."""
import hashlib
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
from app.jobs.task_progress import progress_details

MAX_BYTES = 48 * 1024
MAX_LINES = 300
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def _record(path):
    try:
        # Status files are small; never load a dataset or a whole native log here.
        if path.stat().st_size > 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding='utf8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _child(directory, *parts):
    target = directory.joinpath(*parts).resolve()
    return target if target.is_relative_to(directory.resolve()) else None


def snapshot(directory, source='auto', cursor=''):
    directory = Path(directory).resolve()
    views = _record(directory / 'views_status.json')
    poses = _record(directory / 'pose_status.json')
    model = _record(directory / '3d_status.json')
    records = {'views': views, 'poses': poses, 'model': model}
    active = next((key for key, status in records.items()
                   if status.get('state') in {'running', 'queued'}), None)
    latest = max(records, key=lambda key: str(records[key].get('updated_at') or
                  records[key].get('finished_at') or records[key].get('started_at') or ''))
    stage = active or latest
    files = {}

    def add(key, label, path, record):
        if path and path.is_file() and path.resolve().is_relative_to(directory.resolve()):
            files[key] = (label, path, record)

    view_run = str(views.get('run_id') or '')
    model_run = str(model.get('run_id') or '')
    view_dir = _child(directory, 'view_runs', view_run) if view_run else None
    model_dir = _child(directory, 'model_runs', model_run) if model_run else directory
    add('views', 'Views · ขั้นตอน / ความคืบหน้า', view_dir / 'process.log' if view_dir else None, views)
    add('views-worker', 'Views · worker log',
        view_dir / 'views.log' if view_dir and (view_dir / 'views.log').is_file() else directory / 'views.log', views)
    add('poses', 'Camera Pose · COLMAP', directory / 'poses.log', poses)
    add('model', 'Model · COLMAP / Masks / Train / Export', model_dir / '3d.log' if model_dir else None, model)
    add('model-process', 'Model · ขั้นตอน / ความคืบหน้า', model_dir / 'process.log' if model_dir else None, model)
    defaults = {'views': ('views', 'views-worker'), 'poses': ('poses',), 'model': ('model', 'model-process')}
    if progress_details(model)['phase_key'] in {'preparation', 'dataset', 'masks'}:
        # Mask callbacks update the journal; native COLMAP/Brush output can be quiet here.
        defaults['model'] = ('model-process', 'model')
    selected = next((key for key in defaults.get(stage, ()) if key in files), None) if source == 'auto' else source
    # A queued/new run must not silently show another stage's old log.
    if not selected and not active and files:
        selected = next(iter(files))
    if source != 'auto' and source not in {'views', 'views-worker', 'poses', 'model', 'model-process'}:
        raise ValueError('ไม่พบ log ที่เลือก')
    status = files[selected][2] if selected in files else records[stage]
    result = {'sources': [{'key': key, 'label': item[0]} for key, item in files.items()],
              'source': selected, 'stage': stage, 'state': status.get('state', 'waiting'),
              'busy': bool(active), 'step': str(status.get('step', '')),
              'text': '', 'cursor': '', 'unchanged': False, 'truncated': False,
              'modified_at': None, 'path': None}
    if selected not in files:
        return result
    path = files[selected][1]
    try:
        with path.open('rb') as handle:
            stat = os.fstat(handle.fileno())
            relative = path.relative_to(directory).as_posix()
            identity = f'{relative}:{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}'
            token = hashlib.sha256(identity.encode()).hexdigest()
            result.update(cursor=token, path=relative,
                          modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat())
            if cursor == token:
                result['unchanged'] = True
                return result
            offset = max(0, stat.st_size - MAX_BYTES)
            handle.seek(offset)
            data = handle.read(MAX_BYTES)
        if offset:
            # Drop the first partial line and any partial UTF-8 code point with it.
            boundary = data.find(b'\n')
            if boundary >= 0:
                data = data[boundary + 1:]
        lines = ANSI.sub('', data.decode('utf8', errors='replace')).replace('\r\n', '\n').replace('\r', '\n').splitlines()
        result.update(text='\n'.join(lines[-MAX_LINES:]), truncated=bool(offset or len(lines) > MAX_LINES))
    except OSError:
        # A producer may replace/remove a log between discovering and opening it.
        result.update(cursor='', path=None, modified_at=None)
    return result


def console_markup(job):
    return f'''<section class="panel live-log-panel" data-live-log="{html.escape(job, quote=True)}">
<details open><summary>Console · การทำงานล่าสุด</summary>
<div class="live-log-toolbar"><label>แสดง log <select data-log-source aria-label="เลือก log"><option value="auto">ติดตามงานปัจจุบันอัตโนมัติ</option></select></label>
<button type="button" data-log-pause aria-pressed="false">พักการอัปเดต</button><button type="button" data-log-latest>↓ ข้อความล่าสุด</button></div>
<p class="muted" data-log-step></p><p class="muted" data-log-info></p>
<pre class="live-log-output" data-log-output tabindex="0" role="region" aria-label="ข้อความ log ล่าสุด">กำลังโหลด log…</pre>
<div class="live-log-footer"><span data-log-status role="status">กำลังเชื่อมต่อ…</span><button type="button" data-log-retry hidden>ลองใหม่</button><a data-log-file hidden target="_blank" rel="noopener">เปิด log เต็ม</a></div>
<p class="muted">แสดงท้ายไฟล์สูงสุด 300 บรรทัด · เลื่อนขึ้นเพื่ออ่านข้อความเดิม หรือพักการอัปเดตได้ · หากไม่มีข้อความใหม่ ไม่ได้แปลว่างานหยุด</p>
</details></section><script type="module" src="/assets/job_logs.mjs"></script>'''
