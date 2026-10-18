"""Approximate spherical pose coverage from native mapper events.

COLMAP logs num_reg_frames before trying the next image. This is a coverage
counter, not a linear measure of time: global BA and final refinement can take
much longer as the reconstruction grows.
"""
from collections import OrderedDict
import os
from pathlib import Path
import re
from threading import Lock

_LOG_CACHE = OrderedDict()
_LOG_LOCK = Lock()
_MAX_LOG_BYTES = 256 * 1024


def pose_log_details(path, status):
    """Read bounded tails, keeping the last count through long, noisy BA solves."""
    path = Path(path)
    key = str(path.resolve())
    with _LOG_LOCK:
        try:
            with path.open('rb') as handle:
                stat = os.fstat(handle.fileno())
                identity = (stat.st_dev, stat.st_ino)
                previous = _LOG_CACHE.pop(key, None)
                same_file = (previous and previous['identity'] == identity
                             and stat.st_size >= previous['size']
                             and (stat.st_size != previous['size'] or stat.st_mtime_ns == previous['mtime']))
                if same_file and stat.st_size == previous['size'] and stat.st_mtime_ns == previous['mtime']:
                    tail = previous['tail']
                else:
                    offset = max(0, stat.st_size - _MAX_LOG_BYTES)
                    handle.seek(offset)
                    data = handle.read(_MAX_LOG_BYTES)
                    if offset:
                        data = data.partition(b'\n')[2]  # Drop a partial native line.
                    tail = data.decode('utf8', errors='replace')
                carried = previous['counts'] if same_file else {}
                details = pose_details({**status, **carried}, tail)
                counts = {name: details[name] for name in ('registered_positions_so_far', 'pose_activity') if name in details}
                _LOG_CACHE[key] = dict(identity=identity, size=stat.st_size, mtime=stat.st_mtime_ns, tail=tail, counts=counts)
                while len(_LOG_CACHE) > 64:
                    _LOG_CACHE.popitem(last=False)
                return details
        except OSError:
            return pose_details(status)


def pose_details(status, log=''):
    result = {}
    # A fallback matcher can start another mapper in the same log. Never show
    # the preceding reconstruction's count while the new one is initializing.
    commands = list(re.finditer(r'(?m)^\$[^\n]*\s(mapper|bundle_adjuster|point_filtering)\s[^\n]*', log))
    initial = status.get('pose_initial_registered_positions')
    count = status.get('registered_positions_so_far', initial)
    activity = status.get('pose_activity', 'initializing')
    if commands:
        command = commands[-1]
        log = log[command.end():]
        if command.group(1) != 'mapper':
            # Staged mapping: an external solve between mapper segments keeps the count.
            count = status.get('pose_segment_registered_positions', count)
            activity = 'global_bundle_adjustment'
        else:
            # Staged segments continue from their own checkpoint, not the job's first model.
            count = status.get('pose_segment_registered_positions', initial) if '--input_path' in command.group() else 0
            activity = 'initializing'
    # Native stdout can flush halfway through a number (e.g. "num_reg_frames=55"
    # before the rest of "559)" reaches disk). Require the closing delimiter.
    events = list(re.finditer(r'num_reg_frames=(\d+)\)|(?:Retriangulation and )?Global bundle adjustment', log))
    for event in events:
        if event.group(1) is not None:
            count = int(event.group(1))
            activity = 'registering'
        else:
            activity = 'global_bundle_adjustment'
    total = status.get('pose_total_positions') or (status.get('capture_plan') or {}).get('positions')
    if total and total > 0:
        result['pose_total_positions'] = total
    if count is not None:
        count = max(0, int(count))
        if total and total > 0:
            count = min(count, total)
            result['pose_coverage_percent'] = round(100 * count / total, 1)
        result['registered_positions_so_far'] = count
    result['pose_activity'] = activity
    result['pose_progress_note'] = 'สัดส่วนตำแหน่งโดยประมาณ · ช่วงปรับ BA เปอร์เซ็นต์อาจหยุดชั่วคราว และยังมี BA ตอนท้าย'
    if count is not None and status.get('state') == 'running':
        positions = f'{count:,}/{total:,}' if total and total > 0 else f'{count:,}'
        action = 'กำลังปรับ Global BA' if activity == 'global_bundle_adjustment' else 'กำลังหา Camera Pose'
        result['step'] = f'COLMAP: {action} · ลงทะเบียนแล้วประมาณ {positions} ตำแหน่ง'
    return result
