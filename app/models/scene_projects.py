"""Persistent scene compositions and simple collision boxes.

Scenes reference the existing Gaussian assets; saving a scene never rewrites a
model or claims to reconstruct a surface mesh. Revision checks protect concurrent
editor tabs, and the store lock also coordinates separate backend processes.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

from app.jobs.task_history import utc_now
from app.paths import WORKSPACE


SCENES = WORKSPACE / 'web_data' / 'scenes'
MAX_OBJECTS = 128
MAX_COLLIDERS = 512
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
_lock = threading.RLock()
_SCENE_ID = re.compile(r'[a-f0-9]{32}')
_ITEM_ID = re.compile(r'[A-Za-z0-9_-]{1,80}')


class SceneConflict(ValueError):
    """The editor is attempting to overwrite a newer scene revision."""

    def __init__(self, current_revision):
        self.current_revision = current_revision
        super().__init__('Scene changed in another editor. Reload before saving.')


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    return value


def _number(value, label, minimum=-1e6, maximum=1e6):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not minimum <= value <= maximum or not math.isfinite(value)):
        raise ValueError(f'{label} must be a finite number from {minimum} to {maximum}')
    return value


def _vector(value, label, minimum=-1e6, maximum=1e6):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f'{label} must contain three numbers')
    return [_number(item, label, minimum, maximum) for item in value]


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f'{label} must be true or false')
    return value


def _name(value, label):
    if (not isinstance(value, str) or not value.strip() or len(value) > 160
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f'{label} must contain 1 to 160 printable characters')
    return value.strip()


def _item_id(value):
    if not isinstance(value, str) or not _ITEM_ID.fullmatch(value):
        raise ValueError('Invalid scene object or collider ID')
    return value


def _revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**53 - 1:
        raise ValueError('A positive integer scene revision is required')
    return value


def _scene_path(identifier):
    if not isinstance(identifier, str) or not _SCENE_ID.fullmatch(identifier):
        raise ValueError('Invalid scene ID')
    root = Path(SCENES).resolve()
    target = root / (identifier + '.json')
    if target.resolve().parent != root:
        raise ValueError('Invalid scene path')
    return target


@contextmanager
def _store_lock():
    """Serialize revision read/write pairs across threads and local processes."""
    with _lock:
        root = Path(SCENES).resolve()
        root.mkdir(parents=True, exist_ok=True)
        lock_path = root / '.lock'
        if lock_path.resolve().parent != root:
            raise ValueError('Invalid scene lock path')
        with lock_path.open('a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                if stream.seek(0, os.SEEK_END) == 0:
                    stream.write(b'\0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == 'nt':
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _asset(value, asset_resolver):
    value = _mapping(value, 'asset')
    kind, ref = value.get('kind'), value.get('ref')
    if not isinstance(kind, str) or kind not in {'ply', 'spz', 'glb'}:
        raise ValueError('Asset kind must be ply, spz or glb')
    if (not isinstance(ref, str) or not 1 <= len(ref) <= 512 or ref in {'.', '..'}
            or any(char in ref for char in '/\\:')
            or any(ord(char) < 32 for char in ref)):
        raise ValueError('Invalid asset reference')
    if asset_resolver is not None:
        try:
            resolved = asset_resolver(kind, ref)
        except (FileNotFoundError, ValueError):
            raise ValueError(f'Asset does not exist: {ref}') from None
        if resolved is None or resolved is False:
            raise ValueError(f'Asset does not exist: {ref}')
    return {'kind': kind, 'ref': ref}


def _validate(payload, asset_resolver):
    payload = _mapping(payload, 'Scene')
    version = payload.get('version', 1)
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ValueError('Unsupported scene version')
    units = payload.get('units', 'relative')
    if not isinstance(units, str) or units not in {'relative', 'meters'}:
        raise ValueError('Scene units must be relative or meters')
    objects = payload.get('objects', [])
    colliders = payload.get('colliders', [])
    if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
        raise ValueError(f'A scene supports up to {MAX_OBJECTS} models')
    if not isinstance(colliders, list) or len(colliders) > MAX_COLLIDERS:
        raise ValueError(f'A scene supports up to {MAX_COLLIDERS} colliders')
    identifiers = set()

    def identifier(item):
        item_id = _item_id(item.get('id', uuid.uuid4().hex))
        if item_id in identifiers:
            raise ValueError('Scene object and collider IDs must be unique')
        identifiers.add(item_id)
        return item_id

    validated_objects = []
    for index, item in enumerate(objects):
        item = _mapping(item, 'Model')
        validated_objects.append({
            'id': identifier(item),
            'name': _name(item.get('name', f'Model {index + 1}'), 'Model name'),
            'asset': _asset(item.get('asset'), asset_resolver),
            'position': _vector(item.get('position', [0, 0, 0]), 'Model position'),
            'rotation': _vector(item.get('rotation', [0, 0, 0]), 'Model rotation'),
            'scale': _number(item.get('scale', 1), 'Model scale', 1e-6),
            'visible': _boolean(item.get('visible', True), 'Model visibility'),
        })
    validated_colliders = []
    object_ids = {item['id'] for item in validated_objects}
    for index, item in enumerate(colliders):
        item = _mapping(item, 'Collider')
        kind = item.get('kind', 'floor')
        if not isinstance(kind, str) or kind not in {'floor', 'wall'}:
            raise ValueError('Collider kind must be floor or wall')
        floor = kind == 'floor'
        parent = item.get('parent')
        if parent is not None and (not isinstance(parent, str) or parent not in object_ids):
            raise ValueError('Collider parent must reference a model in this scene')
        rotation = _vector(item.get('rotation', [0,item.get('yaw',0),0]), 'Collider rotation')
        validated_colliders.append({
            'id': identifier(item),
            'name': _name(item.get('name', f'{kind.title()} {index + 1}'), 'Collider name'),
            'kind': kind,
            'position': _vector(item.get('position', [0, -.1, 0] if floor else [0, 1.5, -5]), 'Collider position', -1e12 if parent else -1e6, 1e12 if parent else 1e6),
            'yaw': _number(item.get('yaw', 0), 'Collider yaw'),
            'rotation': rotation,
            'parent': parent,
            'size': _vector(item.get('size', [10, .2, 10] if floor else [5, 3, .2]), 'Collider size', 1e-9 if parent else .001, 1e12 if parent else 1e6),
            'enabled': _boolean(item.get('enabled', True), 'Collider enabled'),
        })
    view = _mapping(payload.get('view', {}), 'View')
    spawn = _mapping(payload.get('spawn', {}), 'Spawn')
    player = _mapping(payload.get('player', {}), 'Player')
    return {
        'version': 1,
        'name': _name(payload.get('name', 'Untitled scene'), 'Scene name'),
        'units': units,
        'objects': validated_objects,
        'colliders': validated_colliders,
        'view': {
            'position': _vector(view.get('position', [6, 4, 6]), 'View position'),
            'target': _vector(view.get('target', [0, 0, 0]), 'View target'),
        },
        'spawn': {
            'position': _vector(spawn.get('position', [0, 1.65, 0]), 'Spawn position'),
            'yaw': _number(spawn.get('yaw', 0), 'Spawn yaw'),
        },
        'player': {
            'height': _number(player.get('height', 1.65), 'Player height', .1, 100),
            'radius': _number(player.get('radius', .25), 'Player radius', .01, 50),
            'speed': _number(player.get('speed', 1.4), 'Player speed', .01, 100),
        },
    }


def _write_scene(document):
    path = _scene_path(document['id'])
    encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    if len(encoded.encode('utf8')) > MAX_DOCUMENT_BYTES:
        raise ValueError('Scene document is too large')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=path.parent,
                                         prefix='.scene-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_scene(payload, asset_resolver):
    """Create a validated composition, resolving each referenced source asset."""
    document = _validate(payload, asset_resolver)
    stamp = utc_now()
    document.update(id=uuid.uuid4().hex, revision=1, created_at=stamp, updated_at=stamp)
    with _store_lock():
        _write_scene(document)
    return document


def read_scene(identifier):
    """Read a scene even when one of its source models was subsequently deleted."""
    path = _scene_path(identifier)
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError('Scene document is too large')
    try:
        document = json.loads(path.read_text(encoding='utf8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError('Scene document is invalid') from None
    if not isinstance(document, dict) or document.get('id') != identifier:
        raise ValueError('Scene document ID does not match its filename')
    _revision(document.get('revision'))
    validated = _validate(document, None)
    validated.update(id=identifier, revision=document['revision'],
                     created_at=document.get('created_at', ''), updated_at=document.get('updated_at', ''))
    return validated


def list_scenes():
    """List valid scene documents; interrupted or unrelated files are ignored."""
    documents = []
    for path in Path(SCENES).glob('*.json'):
        try:
            documents.append(read_scene(path.stem))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(documents, key=lambda doc: (str(doc['updated_at']), doc['id']), reverse=True)


def save_scene(identifier, payload, asset_resolver):
    """Replace a scene only if payload.revision still matches the stored copy."""
    _scene_path(identifier)
    _mapping(payload, 'Scene')
    revision = _revision(payload.get('revision'))
    if 'id' in payload and payload['id'] != identifier:
        raise ValueError('Scene ID does not match the requested scene')
    document = _validate(payload, asset_resolver)
    with _store_lock():
        existing = read_scene(identifier)
        if revision != existing['revision']:
            raise SceneConflict(existing['revision'])
        document.update(id=identifier, revision=revision + 1,
                        created_at=existing['created_at'], updated_at=utc_now())
        _write_scene(document)
    return document


def delete_scene(identifier, revision):
    """Remove a scene after checking the caller's revision; assets are retained."""
    _scene_path(identifier)
    revision = _revision(revision)
    with _store_lock():
        existing = read_scene(identifier)
        if revision != existing['revision']:
            raise SceneConflict(existing['revision'])
        _scene_path(identifier).unlink()
    return existing
