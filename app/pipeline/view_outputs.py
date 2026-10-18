"""Write each Views output once and record it, so later stages reuse exact bytes.

Views already decodes every sampled panorama. It also stores the 1920-wide COLMAP
keyframe (the recipe extract_spherical_keyframes uses), so COLMAP need not decode
the video again, and records each perspective JPEG so dataset preparation can link
it instead of decoding and re-encoding. Every file is replaced atomically: other
stages hold hardlinks to these inodes and must never see them rewritten in place.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

import cv2

DIRECTORY = '.view_outputs'
VIEW_QUALITY = 90
PANORAMA_WIDTH = 1920
PANORAMA_QUALITY = 94
VIEW_RECIPE = f'opencv-imencode-jpeg{VIEW_QUALITY}:{cv2.__version__}'
PANORAMA_RECIPE = f'opencv-inter_area-max{PANORAMA_WIDTH}-jpeg{PANORAMA_QUALITY}:{cv2.__version__}'


def keyframe(panorama):
    height, width = panorama.shape[:2]
    if width > PANORAMA_WIDTH:
        target_height = max(1, round(height * PANORAMA_WIDTH / width))
        panorama = cv2.resize(panorama, (PANORAMA_WIDTH, target_height), interpolation=cv2.INTER_AREA)
    return panorama


def encode(image, quality):
    ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError('เข้ารหัส JPEG ไม่ได้')
    return data.tobytes()


def panorama_name(frame_index):
    return f'pano_{int(frame_index):06d}.jpg'


def atomic_write(path, data):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(data)
        for attempt in range(10):
            try:
                temporary.replace(path)
                break
            except PermissionError:  # Windows: a reader may briefly hold the old file.
                if attempt == 9:
                    raise
                time.sleep(.02)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, record):
    atomic_write(path, json.dumps(record, ensure_ascii=False).encode('utf8'))


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}


def source_identity(source):
    """Cheap identity of the video: size, mtime and both 64 KiB ends."""
    source = Path(source)
    value = source.stat()
    digest = hashlib.sha256(str(value.st_size).encode())
    with source.open('rb') as handle:
        digest.update(handle.read(65536))
        if value.st_size > 65536:
            handle.seek(max(65536, value.st_size - 65536))
            digest.update(handle.read(65536))
    return {'name': source.name, 'size': value.st_size, 'modified_ns': value.st_mtime_ns,
            'fingerprint': digest.hexdigest()}


def view_manifest(view_dir):
    """Perspective JPEGs written by the current Views run: name -> {bytes, sha256}."""
    manifest = read_json(Path(view_dir) / DIRECTORY / 'views.json')
    if manifest.get('schema') != 1 or manifest.get('recipe') != VIEW_RECIPE:
        return {}
    return manifest.get('images') or {}


class ViewOutputs:
    """Thread-safe writer for one Views run. A run first discards older manifests."""

    def __init__(self, output, source, plan):
        self.output = Path(output)
        self.root = self.output / DIRECTORY
        self.source = Path(source)
        self.plan = plan
        self.lock = threading.Lock()
        self.views, self.panoramas = {}, {}
        self.panorama_size = None
        # Invalidate before touching any file, so an interrupted run can never be reused.
        for name in ('views.json', 'panoramas.json'):
            (self.root / name).unlink(missing_ok=True)
        if (self.root / 'panoramas').is_dir():
            shutil.rmtree(self.root / 'panoramas')
        (self.root / 'panoramas').mkdir(parents=True, exist_ok=True)

    def view(self, name, image):
        data = encode(image, VIEW_QUALITY)
        atomic_write(self.output / name, data)
        with self.lock:
            self.views[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def panorama(self, frame_index, image):
        image = keyframe(image)
        data = encode(image, PANORAMA_QUALITY)
        path = self.root / 'panoramas' / panorama_name(frame_index)
        atomic_write(path, data)
        with self.lock:
            self.panorama_size = [int(image.shape[1]), int(image.shape[0])]
            self.panoramas[path.name] = {'frame_index': int(frame_index), 'bytes': len(data),
                                         'sha256': hashlib.sha256(data).hexdigest()}

    def complete(self):
        plan = self.plan or {}
        write_json(self.root / 'views.json', {'schema': 1, 'recipe': VIEW_RECIPE, 'quality': VIEW_QUALITY,
                                              'images': self.views})
        if self.panoramas:
            write_json(self.root / 'panoramas.json', {
                'schema': 1, 'recipe': PANORAMA_RECIPE, 'max_width': PANORAMA_WIDTH,
                'quality': PANORAMA_QUALITY, 'interpolation': 'INTER_AREA',
                'size': self.panorama_size, 'source': source_identity(self.source),
                'sample_every': plan.get('effective_sample_every'), 'start_frame': plan.get('start_frame'),
                'end_frame': plan.get('end_frame'), 'frames': self.panoramas})


def restore_panoramas(view_dir, source, metadata, output_dir):
    """Link cached COLMAP keyframes into output_dir. Returns (count, None) or (None, reason)."""
    root = Path(view_dir) / DIRECTORY
    manifest = read_json(root / 'panoramas.json')
    if not manifest:
        return None, 'ไม่มี panorama cache จากขั้น Views'
    if manifest.get('schema') != 1 or manifest.get('recipe') != PANORAMA_RECIPE:
        return None, 'panorama cache ใช้สูตรเก่า'
    expected = {int(frame['frame_index']) for frame in metadata.get('frames', [])
                if frame.get('frame_index') is not None}
    if len(expected) < 3:
        return None, 'เฟรมน้อยกว่า 3'
    try:
        if manifest.get('source') != source_identity(source):
            return None, 'ไฟล์ต้นฉบับเปลี่ยนหลังสร้าง Views'
    except OSError as exc:
        return None, f'อ่านไฟล์ต้นฉบับไม่ได้: {exc}'
    if int(metadata.get('settings', {}).get('sample_every', 1)) != manifest.get('sample_every'):
        return None, 'sample_every ไม่ตรงกับ Views'
    cached = manifest.get('frames') or {}
    names = [panorama_name(index) for index in sorted(expected)]
    for name in names:
        record = cached.get(name)
        path = root / 'panoramas' / name
        try:
            if not record or path.stat().st_size != record['bytes'] or \
                    hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
                return None, f'panorama cache ไม่ครบหรือไม่ตรง: {name}'
        except (OSError, KeyError, TypeError):
            return None, f'panorama cache ไม่ครบ: {name}'
    output_dir = Path(output_dir)
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    linked = 0
    for name in names:
        try:
            os.link(root / 'panoramas' / name, output_dir / name)
            linked += 1
        except OSError:
            shutil.copy2(root / 'panoramas' / name, output_dir / name)
    return len(names), None if linked == len(names) else f'copied {len(names) - linked} files (hardlink unavailable)'
