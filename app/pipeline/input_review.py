"""Bounded capture checks, conservative frame selection and source-pixel recovery."""
import math
from pathlib import Path
import uuid
from functools import lru_cache
import threading

import cv2
import numpy as np

from app.pipeline.pano_to_views import frames_from_input, perspective_map, project_frame

FLOOR_PRESETS = {'standard': .72, 'more_floor': .9, 'person_only': 1.0}
_source_decoders = threading.BoundedSemaphore(1)


@lru_cache(maxsize=2)
def source_crop(filename, size, modified_ns, index, width, height, fov, yaw, pitch):
    # Decode full source resolution; the regular preview deliberately downsizes 8K.
    with _source_decoders:
        frames = frames_from_input(Path(filename), 1, index, index + 1)
        try:
            _, image = next(frames)
        except StopIteration as exc:
            raise ValueError('อ่านเฟรมต้นฉบับไม่ได้') from exc
        finally:
            frames.close()
        pixels = project_frame(image, width, height, fov, yaw, pitch, True)
        ok, encoded = cv2.imencode('.png', pixels)
        if not ok:
            raise ValueError('สร้างภาพเทียบไม่ได้')
        return encoded.tobytes()


def training_options(values=None):
    values = values or {}
    floor = values.get('floor', 'standard')
    detail = values.get('detail', 'existing')
    if floor not in FLOOR_PRESETS or detail not in {'existing', 'source_2048'}:
        raise ValueError('ตัวเลือกเตรียมภาพไม่ถูกต้อง')
    return {'floor': floor, 'detail': detail, 'filter_blur': values.get('filter_blur', True) in (True, '1', 1)}


def atomic_pixels(path, image):
    path = Path(path)
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise ValueError(f'บันทึกภาพไม่ได้: {path.name}')
    temporary = path.with_name(uuid.uuid4().hex + '.partial')
    try:
        temporary.write_bytes(encoded.tobytes())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_frames(directory, metadata, limit=24):
    groups = {}
    for ordinal, frame in enumerate(metadata.get('frames', [])):
        groups.setdefault(frame.get('frame_index', ordinal), []).append(frame)
    positions = sorted(groups)
    selected = positions if limit is None else [positions[i] for i in np.unique(np.linspace(0, max(0, len(positions)-1), min(limit, len(positions)), dtype=int))]
    samples = []
    for position in selected:
        frame = min(groups[position], key=lambda f: (abs(f.get('pitch_deg') or 0), abs(f.get('yaw_deg') or 0)))
        path = (Path(directory) / frame['file_path']).resolve()
        if not path.is_relative_to(Path(directory).resolve()):
            raise ValueError('พาธภาพไม่ถูกต้อง')
        image = cv2.imread(str(path))
        if image is None:
            continue
        gray = cv2.cvtColor(cv2.resize(image, (256, 256), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        samples.append({'frame_index': position, 'name': path.stem,
                        'sharpness': round(float(cv2.Laplacian(gray, cv2.CV_32F).var()), 3),
                        'dark_fraction': round(float(np.mean(gray < 12)), 3),
                        'bright_fraction': round(float(np.mean(gray > 245)), 3)})
    median = float(np.median([s['sharpness'] for s in samples])) if samples else 0
    for sample in samples:
        sample['soft'] = median > 10 and sample['sharpness'] < median * .15
        sample['warning'] = ('รายละเอียดต่ำกว่าภาพอื่นมาก · ตรวจภาพสั่น/ผนังเรียบ' if sample['soft'] else
                             'ภาพมืดมาก' if sample['dark_fraction'] > .65 else
                             'ส่วนสว่างขาวมาก' if sample['bright_fraction'] > .65 else '')
    return {'samples': samples, 'positions': len(positions), 'sampled_positions': len(samples),
            'median_sharpness': median, 'note': 'ตรวจจากมุมหลักของแต่ละตำแหน่ง เป็นคำแนะนำเบื้องต้น ผนังเรียบอาจได้คะแนนต่ำ'}


def filter_soft_positions(metadata, report, fps):
    frames = metadata['frames']
    positions = sorted({f.get('frame_index', i) for i, f in enumerate(frames)})
    kept, removed = positions.copy(), []
    spacing = max(1, round(float(fps or 30) * 2))
    for sample in sorted(report['samples'], key=lambda s: s['sharpness']):
        position = sample['frame_index']
        if not sample['soft'] or len(kept) <= 6 or len(removed) >= len(positions) // 5 or position % spacing == 0:
            continue
        index = kept.index(position)
        if 0 < index < len(kept) - 1 and kept[index+1] - kept[index-1] <= float(fps or 30) * .3:
            kept.remove(position)
            removed.append(position)
    metadata['frames'] = [f for i, f in enumerate(frames) if f.get('frame_index', i) in kept]
    return sorted(removed)


def recover_source_pixels(source, directory, metadata, progress=None):
    """Reproject the exact source frame and pose; preserve source views and old runs."""
    directory = Path(directory)
    groups = {}
    for frame in metadata['frames']:
        if frame.get('frame_index') is None or frame.get('yaw_deg') is None or frame.get('pitch_deg') is None:
            raise ValueError('โหมดรายละเอียดต้นฉบับต้องใช้ Views จากวิดีโอ 360 ที่มีหมายเลขเฟรมและมุมครบ')
        groups.setdefault(frame['frame_index'], []).append(frame)
    ids = sorted(groups)
    old_width, old_height = int(metadata['w']), int(metadata['h'])
    width = 2048
    height = max(160, round(old_height * width / old_width))
    if height > 2048:
        height, width = 2048, round(old_width * 2048 / old_height)
    fov = math.degrees(2 * math.atan(old_width / (2 * metadata['fl_x'])))
    step = math.gcd(*(b-a for a, b in zip(ids, ids[1:]))) if len(ids) > 1 else 1
    maps, completed = {}, set()
    for index, image in frames_from_input(Path(source), step, ids[0], ids[-1]+1):
        if index not in groups:
            continue
        for frame in groups[index]:
            key = (frame['yaw_deg'], frame['pitch_deg'])
            if key not in maps:
                maps[key] = perspective_map(width, height, fov, *key)[:2]
            projected = project_frame(image, width, height, fov, *key, True, maps[key])
            relative = Path(frame['file_path']).with_suffix('.png')
            target = (directory / relative).resolve()
            if not target.is_relative_to(directory.resolve()):
                raise ValueError('พาธภาพไม่ถูกต้อง')
            atomic_pixels(target, projected)
            frame['file_path'] = relative.as_posix()
            mask = directory / 'masks' / (relative.stem + '.png')
            if mask.is_file():
                pixels = cv2.imread(str(mask), cv2.IMREAD_GRAYSCALE)
                atomic_pixels(mask, cv2.resize(pixels, (width, height), interpolation=cv2.INTER_NEAREST))
        completed.add(index)
        if progress:
            progress(len(completed), len(ids))
    if completed != set(ids):
        raise ValueError('อ่านเฟรมต้นฉบับไม่ครบ จึงยังไม่เริ่มเทรน')
    metadata.update(w=width, h=height, fl_x=metadata['fl_x'] * width / old_width,
                    fl_y=metadata['fl_y'] * height / old_height, cx=(width-1)/2, cy=(height-1)/2)
    return {'format': 'PNG', 'width': width, 'height': height, 'source_frames': len(completed)}


def floor_preview(image, frame, metadata, floor):
    h, w = image.shape[:2]
    fov = math.degrees(2 * math.atan(metadata['w'] / (2 * metadata['fl_x'])))
    _, latitude, _ = perspective_map(w, h, fov, 0, frame.get('pitch_deg') or 0)
    ignored = latitude > (h - 1) * FLOOR_PRESETS[floor]
    output = image.copy()
    output[ignored] = (image[ignored] * .35 + np.array([40, 40, 220]) * .65).astype(np.uint8)
    return output
