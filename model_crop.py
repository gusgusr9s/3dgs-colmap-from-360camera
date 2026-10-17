"""Spatial Gaussian selection and immutable PLY edits. Never changes training inputs."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

import numpy as np
from plyfile import PlyData
from model_variants import sha256_file, source_signature as _source_signature
from model_exports import _write as write_status
from processing_queue import processing_slot, try_lock
from task_history import utc_now, run_id

ROOT = Path(__file__).resolve().parent
EDITS = ROOT / 'web_data' / 'model_edits'
KERNEL_SIGMA = float(np.sqrt(12))
_preview_slots = threading.BoundedSemaphore(2)
REQUIRED = ['x', 'y', 'z', 'opacity', 'scale_0', 'scale_1', 'scale_2',
            'rot_0', 'rot_1', 'rot_2', 'rot_3', 'f_dc_0', 'f_dc_1', 'f_dc_2']


def source_signature(path):
    # Browser JSON numbers cannot round-trip nanosecond timestamps (> 2**53).
    value = _source_signature(path)
    return {**value, 'mtime_ns': str(value['mtime_ns'])}


def model_path(models, name):
    if not isinstance(name, str) or not name or '/' in name or '\\' in name or Path(name).name != name or not name.endswith('.ply'):
        raise ValueError('ชื่อ Model ไม่ถูกต้อง')
    target = Path(models) / name
    if not target.is_file():
        raise ValueError('ไม่พบ Model ต้นทาง')
    return target


def read_vertices(path):
    data = PlyData.read(path, mmap='r')
    vertices = data['vertex'].data
    if not len(vertices) or not set(REQUIRED).issubset(vertices.dtype.names or ()):
        raise ValueError('ต้องใช้ Gaussian PLY ที่มีตำแหน่ง สี opacity scale และ rotation')
    if any(vertices.dtype.fields[name][0].kind not in 'fiu' for name in vertices.dtype.names):
        raise ValueError('ยังไม่รองรับ Gaussian PLY ที่มี list properties')
    return data, vertices


def validate_crop(value):
    try:
        if not isinstance(value, dict):
            raise ValueError()
        origin = np.asarray(value['origin'], dtype=float)
        rotation = np.asarray(value['rotation'], dtype=float)
        lower, upper = np.asarray(value['min'], dtype=float), np.asarray(value['max'], dtype=float)
        strict = value.get('strict', True)
        if (origin.shape != (3,) or lower.shape != (3,) or upper.shape != (3,) or rotation.shape != (3, 3)
                or not isinstance(strict, bool) or not all(np.isfinite(x).all() for x in (origin, rotation, lower, upper))
                or np.max(np.abs(origin)) > 1e12 or np.max(np.abs(lower)) > 1e12 or np.max(np.abs(upper)) > 1e12
                or np.any(upper-lower <= 1e-6)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0)
                or abs(np.linalg.det(rotation)-1) > 1e-6):
            raise ValueError()
        return {'origin': origin.tolist(), 'rotation': rotation.tolist(), 'min': lower.tolist(),
                'max': upper.tolist(), 'strict': strict}
    except (ValueError, KeyError, TypeError, OverflowError):
        raise ValueError('กรอบตัดไม่ถูกต้อง: ขอบต้องไม่ไขว้กัน และ rotation ต้องเป็นกรอบหมุนที่ถูกต้อง') from None


def _gaussian_rotation(q):
    # PLY stores wxyz; output rows are world axes, columns are ellipsoid axes.
    w, x, y, z = q.T
    result = np.empty((len(q), 3, 3))
    result[:, 0, :] = np.column_stack((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)))
    result[:, 1, :] = np.column_stack((2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)))
    result[:, 2, :] = np.column_stack((2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)))
    return result


def select_vertices(vertices, crop, progress=None):
    crop = validate_crop(crop)
    origin, rotation = np.asarray(crop['origin']), np.asarray(crop['rotation'])
    lower, upper = np.asarray(crop['min']), np.asarray(crop['max'])
    keep = np.zeros(len(vertices), dtype=np.uint8)
    for start in range(0, len(vertices), 32768):
        chunk = vertices[start:start+32768]
        positions = np.column_stack([chunk[k] for k in ('x', 'y', 'z')]).astype(float)
        local = (positions-origin) @ rotation
        finite = np.ones(len(chunk), dtype=bool)
        for name in chunk.dtype.names:
            finite &= np.isfinite(chunk[name])
        q = np.column_stack([chunk[f'rot_{k}'] for k in range(4)]).astype(float)
        norms = np.linalg.norm(q, axis=1)
        finite &= norms > 1e-12
        radius = 0
        if crop['strict']:
            q = q / np.maximum(norms[:, None], 1e-12)
            axes = np.einsum('ij,njk->nik', rotation.T, _gaussian_rotation(q))
            with np.errstate(over='ignore', invalid='ignore'):
                scales = np.exp(np.column_stack([chunk[f'scale_{k}'] for k in range(3)]).astype(float))
                radius = KERNEL_SIGMA * np.sqrt(np.sum((axes * scales[:, None, :])**2, axis=2))
            finite &= np.isfinite(radius).all(axis=1)
        selected = finite & (local-radius >= lower).all(axis=1) & (local+radius <= upper).all(axis=1)
        keep[start:start+len(chunk)] = selected
        if progress:
            progress(10 + int(35*(start+len(chunk))/len(vertices)))
    return keep


def basis_from_up(up, initial_view):
    matrix = np.asarray(initial_view.get('transform_matrix', np.eye(4)), dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        matrix = np.eye(4)
    if up is None:
        up = matrix[:3, 1]
        # Captured horizontal cameras can be slightly tilted. Snap only near an axis.
        if np.max(np.abs(up)) > .995:
            up = np.eye(3)[np.argmax(np.abs(up))] * np.sign(up[np.argmax(np.abs(up))])
    up = np.asarray(up, dtype=float)
    if up.shape != (3,) or not np.isfinite(up).all() or np.linalg.norm(up) < 1e-9:
        up = np.array([0., 1., 0.])
    up = up / np.linalg.norm(up)
    right = matrix[:3, 0] - up*np.dot(matrix[:3, 0], up)
    if np.linalg.norm(right) < 1e-6:
        axis = np.eye(3)[np.argmin(np.abs(up))]
        right = axis-up*np.dot(axis, up)
    right /= np.linalg.norm(right)
    return np.column_stack([right, up, np.cross(right, up)])


@lru_cache(maxsize=12)
def _inspect(path, size, modified, basis_flat):
    source = Path(path)
    _, vertices = read_vertices(source)
    sample = vertices[::max(1, len(vertices)//200000)]
    points = np.column_stack([sample[k] for k in ('x', 'y', 'z')]).astype(float)
    points = points[np.isfinite(points).all(axis=1)]
    if not len(points):
        raise ValueError('ไม่พบตำแหน่ง Gaussian ที่ใช้งานได้')
    origin = np.median(points, axis=0)
    rotation = np.asarray(basis_flat).reshape(3, 3)
    local = (points-origin) @ rotation
    lower, upper = np.quantile(local, [.005, .995], axis=0)
    padding = np.maximum((upper-lower)*.08, .01)
    lower -= padding; upper += padding
    if source_signature(source) != {'bytes': size, 'mtime_ns': modified}:
        raise ValueError('Model เปลี่ยนระหว่างอ่าน กรุณาเปิด Editor ใหม่')
    return {'count': len(vertices), 'source_signature': {'bytes': size, 'mtime_ns': modified},
            'source_sha256': sha256_file(source),
            'suggested': {'origin': origin.tolist(), 'rotation': rotation.tolist(),
                          'min': lower.tolist(), 'max': upper.tolist(), 'strict': True},
            'bounds': {'min': np.min(local, axis=0).tolist(), 'max': np.max(local, axis=0).tolist()},
            'kernel_sigma': KERNEL_SIGMA}


def inspect_model(source, initial_view=None, up=None):
    stat = source_signature(source)
    basis = basis_from_up(up, initial_view or {})
    return _inspect(str(source.resolve()), stat['bytes'], stat['mtime_ns'], tuple(basis.flat))


def checked_source(models, payload):
    source = model_path(models, payload.get('model'))
    if source_signature(source) != payload.get('source_signature'):
        raise ValueError('Model ต้นทางเปลี่ยนไป กรุณาเปิด Editor ใหม่')
    return source


def preview(models, payload):
    source = checked_source(models, payload)
    if not _preview_slots.acquire(timeout=2):
        raise ValueError('กำลังคำนวณพรีวิวอื่น กรุณาลองอีกครั้ง')
    try:
        _, vertices = read_vertices(source)
        mask = select_vertices(vertices, payload.get('crop'))
        if source_signature(source) != payload['source_signature']:
            raise ValueError('Model เปลี่ยนระหว่างตรวจ กรุณาเปิด Editor ใหม่')
        return mask, hashlib.sha256(mask.tobytes()).hexdigest()
    finally:
        _preview_slots.release()


def edit_directory(identifier):
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        raise ValueError('Invalid edit ID')
    return EDITS / 'records' / identifier


def edit_status(identifier, owner_alive):
    try:
        directory = edit_directory(identifier)
        state = json.loads((directory/'status.json').read_text(encoding='utf8'))
        if state['id'] != identifier:
            return None
        if state['state'] in {'queued', 'running'}:
            active = False
            try:
                with (directory/'.lock').open('r+b') as handle:
                    active = not try_lock(handle)
            except FileNotFoundError:
                pass
            if not active or not owner_alive(directory/'status.json'):
                state = {**state, 'state': 'error', 'error': 'การบันทึกถูกหยุด กรุณาลองอีกครั้ง'}
        return state
    except (ValueError, OSError, KeyError, TypeError):
        return None


def start_edit(models, payload, metadata, owner_alive, on_saved=None):
    source = checked_source(models, payload)
    crop = validate_crop(payload.get('crop'))
    if not re.fullmatch('[a-f0-9]{64}', payload.get('selection_sha256', '')) or not re.fullmatch('[a-f0-9]{64}', payload.get('source_sha256', '')):
        raise ValueError('กรุณารอให้พรีวิวคำนวณเสร็จก่อนบันทึก')
    identifier = payload.get('request_id')
    directory = edit_directory(identifier)
    directory.mkdir(parents=True, exist_ok=True)
    expected = hashlib.sha256(json.dumps({'source': source.name, 'signature': payload['source_signature'],
        'crop': crop, 'source_sha256': payload['source_sha256'], 'selection': payload['selection_sha256']}, sort_keys=True).encode()).hexdigest()
    handle = (directory/'.lock').open('a+b')
    if handle.tell() == 0:
        handle.write(b'0'); handle.flush()
    if not try_lock(handle):
        handle.close()
        state = edit_status(identifier, owner_alive)
        if not state or state.get('request_sha256') != expected:
            raise ValueError('รหัสบันทึกนี้กำลังใช้กับการตัดอีกชุด กรุณารอสักครู่')
        return state
    try:
        previous = edit_status(identifier, owner_alive)
        if previous and previous.get('request_sha256') != expected:
            raise ValueError('รหัสบันทึกซ้ำกับการตัดอีกชุด กรุณาส่งคำขอใหม่')
        if previous and previous['state'] == 'ready':
            if not (Path(models)/previous['model']).is_file():
                raise ValueError('รุ่นที่บันทึกถูกลบแล้ว กรุณาส่งคำขอใหม่')
            handle.close(); return previous
        state = {'id': identifier, 'request_sha256': expected, 'state': 'queued', 'progress': 0,
                 'pid': os.getpid(), 'created_at': utc_now(), 'parent_model': source.name, 'crop': crop}
        write_status(directory, state)
        threading.Thread(target=_save, args=(source, payload, metadata, state, owner_alive, handle, on_saved), daemon=True).start()
        return state
    except BaseException:
        handle.close(); raise


def _save(source, payload, metadata, state, owner_alive, handle, on_saved):
    directory = edit_directory(state['id'])
    temporary = source.parent / ('.crop_'+uuid.uuid4().hex+'.partial')
    started = time.monotonic()
    try:
        with processing_slot(EDITS/'queue', owner_alive):
            state.update(state='running', progress=3); write_status(directory, state)
            if source_signature(source) != payload['source_signature'] or sha256_file(source) != payload['source_sha256']:
                raise ValueError('Model เปลี่ยนไปแล้ว กรุณาเปิด Editor ใหม่')
            data, vertices = read_vertices(source)
            def progress(value):
                state.update(progress=value); write_status(directory, state)
            mask = select_vertices(vertices, state['crop'], progress)
            if hashlib.sha256(mask.tobytes()).hexdigest() != payload['selection_sha256']:
                raise ValueError('ผลเลือกไม่ตรงกับพรีวิว กรุณาตรวจพรีวิวใหม่')
            kept = int(mask.sum())
            if not kept:
                raise ValueError('กรอบนี้ไม่เหลือจุด กรุณาขยายกรอบก่อนบันทึก')
            # Preserve every vertex property and its numeric type. Write selected chunks,
            # avoiding a second full-sized copy for multi-million-splat models.
            byte_order = 'big_endian' if data.byte_order == '>' else 'little_endian'
            header = f'ply\nformat binary_{byte_order} 1.0\nelement vertex {kept}\n'
            header += '\n'.join(str(prop) for prop in data['vertex'].properties) + '\nend_header\n'
            with temporary.open('xb') as stream:
                stream.write(header.encode('ascii'))
                for start in range(0, len(vertices), 32768):
                    chunk = vertices[start:start+32768]
                    stream.write(chunk[mask[start:start+len(chunk)].astype(bool)].tobytes())
                    progress(45+int(40*(start+len(chunk))/len(vertices)))
            if source_signature(source) != payload['source_signature'] or sha256_file(source) != payload['source_sha256']:
                raise ValueError('Model เปลี่ยนระหว่างบันทึก กรุณาลองอีกครั้ง')
            target = source.parent / f'{source.stem[:80]}_crop_{run_id()}.ply'
            edit = {'type': 'spatial_crop', 'parent_model': source.name, 'source_sha256': payload['source_sha256'],
                    'selection_sha256': payload['selection_sha256'], 'crop': state['crop'],
                    'kernel_sigma': KERNEL_SIGMA, 'input_splats': len(vertices), 'kept_splats': kept,
                    'removed_splats': len(vertices)-kept, 'edit_id': state['id']}
            # Keep input lineage/camera access, but never claim the parent's evaluation scores
            # or checkpoint are a quality evaluation / checkpoint of this edited model.
            fields = ['job','run_id','view_run_id','source_key','source_name','source_profile','view_settings',
                      'settings','selected_frames','selection_signature','viewer_config']
            meta = {k: metadata[k] for k in fields if k in metadata}
            meta.update(model=target.name, label=source.stem+' · ตัดขอบ', created_at=utc_now(), recorded_at=utc_now(),
                        state='done', stage='model_edit', edit=edit, quality={'published_splats': kept},
                        evaluation_note='ยังไม่ได้ประเมิน Render ของรุ่นหลังตัดขอบ',
                        parent_quality=metadata.get('quality', {}))
            meta['world_up'] = np.asarray(state['crop']['rotation'])[:, 1].tolist()
            matrix = np.asarray(meta.get('viewer_config', {}).get('transform_matrix', []))
            if matrix.shape == (4, 4):
                local = (matrix[:3, 3]-np.asarray(state['crop']['origin'])) @ np.asarray(state['crop']['rotation'])
                if np.any(local < state['crop']['min']) or np.any(local > state['crop']['max']):
                    meta['viewer_config'] = {}
            meta_path = target.with_suffix('.meta.json')
            with meta_path.open('x', encoding='utf8') as stream:
                json.dump(meta, stream, ensure_ascii=False, indent=2)
            try:
                # Same directory/filesystem: exclusive atomic publication, never replaces a model.
                os.link(temporary, target)
            except BaseException:
                meta_path.unlink(missing_ok=True); raise
            state.update(state='ready', progress=100, model=target.name, edit=edit, bytes=target.stat().st_size,
                         sha256=sha256_file(target), finished_at=utc_now(), duration_seconds=time.monotonic()-started)
            if on_saved:
                try:
                    state['compression_id'] = on_saved(target)
                except Exception as exc:
                    state['compression_error'] = str(exc)
            write_status(directory, state)
    except Exception as exc:
        state.update(state='error', error=str(exc), finished_at=utc_now()); write_status(directory, state)
    finally:
        temporary.unlink(missing_ok=True)
        handle.close()
