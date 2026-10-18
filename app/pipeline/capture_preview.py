"""Exact clip/sampling plan and bounded, on-demand source image previews."""

from app.paths import ROOT
from app.runtime.python_command import module_command
from functools import lru_cache
from pathlib import Path
import math
import threading
import json
import os
import struct
import subprocess
import sys

import cv2
import numpy as np

from app.pipeline.pano_to_views import panorama_angles, project_frame

DECODERS = threading.BoundedSemaphore(1)


class PreviewBusy(RuntimeError):
    pass


def preview_worker(mode, source, *args):
    try:
        result = subprocess.run(module_command('app.runtime.capture_preview_worker',mode,source,*args), capture_output=True, timeout=25,cwd=ROOT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                env={**os.environ, 'OMP_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1'})
    except subprocess.TimeoutExpired as exc:
        raise ValueError('อ่านพรีวิวเกิน 25 วินาที ลองช่วงต้นคลิปหรือกดลองใหม่') from exc
    if result.returncode:
        raise ValueError('อ่านพรีวิวไม่ได้: ' + result.stderr.decode('utf8', errors='replace')[-400:])
    return result.stdout


@lru_cache(maxsize=128)
def _preview_profile(path, size, modified_ns):
    if not DECODERS.acquire(timeout=.1):
        raise PreviewBusy('พรีวิวกำลังอ่านภาพอื่นอยู่ กรุณารอสักครู่แล้วกดลองใหม่ (แยกจากคิว Model)')
    try:
        return json.loads(preview_worker('profile', path))
    finally:
        DECODERS.release()


def preview_profile(source):
    stat = source.stat()
    return _preview_profile(str(source.resolve()), stat.st_size, stat.st_mtime_ns)


def sampling_plan(profile, settings):
    start = float(settings.get('start_seconds', 0) or 0)
    end = float(settings.get('end_seconds', 0) or 0)
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < 0:
        raise ValueError('เวลาเริ่มและเวลาจบต้องเป็นตัวเลขตั้งแต่ 0 ขึ้นไป')
    video = profile['kind'] == 'video'
    fps, count = profile.get('fps'), profile.get('frame_count')
    duration = profile.get('duration_seconds')
    if (start or end) and (not video or not fps or not count):
        raise ValueError('เลือกช่วงเวลาได้เฉพาะวิดีโอที่อ่าน FPS และจำนวนเฟรมได้')
    if end and (end <= start or (duration and end > duration + 1e-6)):
        raise ValueError('เวลาจบต้องมากกว่าเวลาเริ่มและไม่เกินความยาววิดีโอ')
    if duration and start >= duration:
        raise ValueError('เวลาเริ่มต้องอยู่ภายในวิดีโอ')
    start_frame = math.ceil(start * fps - 1e-8) if video and fps else 0
    end_frame = min(count, math.ceil(end * fps - 1e-8)) if end else count
    if end_frame is not None and start_frame >= end_frame:
        raise ValueError('ช่วงที่เลือกไม่มีเฟรม กรุณาขยายช่วงเวลา')
    requested = max(1, int(settings.get('sample_every', 1)))
    interval = min(requested, max(1, int(fps * .3))) if video and fps else requested
    positions = math.ceil((end_frame - start_frame) / interval) if end_frame is not None else None
    panorama = settings.get('projection', 'auto') == 'panorama' or (
        settings.get('projection', 'auto') == 'auto' and profile['detected_projection'] == 'panorama')
    if panorama and abs(profile['aspect_ratio'] - 2) >= .08:
        raise ValueError('ภาพนี้ไม่ใช่ 360 แบบ 2:1 กรุณาใช้ไฟล์ Export แบบ 360 Video')
    directions = panorama_angles(int(settings.get('views', 8)), settings.get('quality', 'high')) if panorama else [(0., 0.)]
    return {'start_frame': start_frame, 'end_frame': end_frame,
            'effective_sample_every': interval, 'requested_sample_every': requested,
            'start_seconds': start_frame / fps if video and fps else 0,
            'end_seconds': end_frame / fps if video and fps and end_frame is not None else None,
            'sample_gap_seconds': interval / fps if video and fps else None,
            'positions': positions, 'views_per_frame': len(directions),
            'total_views': positions * len(directions) if positions else None,
            'panorama': panorama,
            'directions': [{'yaw': yaw, 'pitch': pitch} for yaw, pitch in directions]}


@lru_cache(maxsize=8)
def decoded_frame(path, size, modified_ns, index):
    # The caller holds DECODERS around cache access, so raw/crop cannot decode twice.
    data = preview_worker('frame', path, index)
    if len(data) < 8:
        raise ValueError('ข้อมูลพรีวิวไม่ครบ')
    height, width = struct.unpack('<II', data[:8])
    if not (0 < height <= 2048 and 0 < width <= 2048) or len(data) != 8 + height*width*3:
        raise ValueError('ข้อมูลพรีวิวไม่ครบ')
    return np.frombuffer(data, dtype=np.uint8, offset=8).reshape(height, width, 3)


def preview_jpeg(source, profile, settings, ordinal=0, direction=0, size=512, raw=False):
    plan = sampling_plan(profile, settings)
    if ordinal < 0 or ordinal >= (plan['positions'] or 1):
        raise ValueError('ตำแหน่งพรีวิวอยู่นอกช่วงที่เลือก')
    if direction < 0 or direction >= len(plan['directions']):
        raise ValueError('มุมพรีวิวไม่ถูกต้อง')
    index = plan['start_frame'] + ordinal * plan['effective_sample_every']
    stat = source.stat()
    if not DECODERS.acquire(timeout=.1):
        raise PreviewBusy('พรีวิวกำลังอ่านภาพอื่นอยู่ กรุณารอสักครู่แล้วกดลองใหม่ (แยกจากคิว Model)')
    try:
        image = decoded_frame(str(source.resolve()), stat.st_size, stat.st_mtime_ns, index)
    finally:
        DECODERS.release()
    if raw:
        ratio = min(1, size / max(image.shape[:2]))
        image = cv2.resize(image, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
    else:
        width, height = int(settings['width']), int(settings['height'])
        ratio = min(1, size / max(width, height))
        width, height = max(2, round(width * ratio)), max(2, round(height * ratio))
        angle = plan['directions'][direction]
        image = project_frame(image, width, height, float(settings['fov']),
                              angle['yaw'], angle['pitch'], plan['panorama'])
    ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise ValueError('สร้างภาพพรีวิวไม่ได้')
    return data.tobytes(), plan, index
