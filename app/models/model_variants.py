"""Immutable, cached SPZ downloads for the existing PLY models."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import threading
import time
import uuid
import re
from app.jobs.task_history import utc_now

from app.paths import ROOT, WORKSPACE
VARIANTS = WORKSPACE / 'web_data' / 'model_variants'
_lock = threading.Lock()
_queue_lock = threading.Lock()
_pending = {}


def write_record_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def compression_directory(identifier):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('Invalid compression ID')
    return VARIANTS / 'records' / identifier


def compression_record(identifier):
    try:
        record = json.loads((compression_directory(identifier) / 'record.json').read_text(encoding='utf8'))
        return record if isinstance(record, dict) and record.get('id') == identifier else None
    except (ValueError, OSError):
        return None


def compression_records():
    result = []
    for path in (VARIANTS / 'records').glob('*/record.json'):
        record = compression_record(path.parent.name)
        if record:
            result.append(record)
    return sorted(result, key=lambda r: r.get('created_at', ''), reverse=True)


def new_record(source, identifier=None):
    try:
        parent_info = json.loads(source.with_suffix('.meta.json').read_text(encoding='utf8'))
    except (OSError, ValueError):
        parent_info = {'model': source.name}
    return {'id': identifier or uuid.uuid4().hex, 'state': 'queued', 'kind': 'compressed_model',
            'model': source.name, 'parent_model': source.name, 'parent_info': parent_info,
            'created_at': utc_now(), 'format': 'spz', 'preset': 'detail', 'source': source_signature(source), 'pid': os.getpid()}


def register_variant(source, result, identifier=None):
    """Keep each download and its lineage even when the latest cache changes."""
    if result.get('record_id') and compression_record(result['record_id']):
        return result
    record = compression_record(identifier) if identifier else None
    record = record or new_record(source, identifier)
    record.update(result, id=record['id'], state='ready', finished_at=utc_now())
    directory = compression_directory(record['id'])
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'model.spz'
    if not target.exists():
        try:
            os.link(variant_directory(source.name) / 'fast.spz', target)
        except OSError:
            shutil.copy2(variant_directory(source.name) / 'fast.spz', target)
    write_record_json(directory / 'record.json', record)
    result = {**result, 'record_id': record['id']}
    write_record_json(variant_directory(source.name) / 'fast.json', result)
    return result


def source_signature(path):
    stat = path.stat()
    return {'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def variant_directory(model):
    if Path(model).name != model or not model.endswith('.ply') or '/' in model or '\\' in model:
        raise ValueError('Invalid model name')
    stem = Path(model).stem
    if stem in {'.', '..', ''}:
        raise ValueError('Invalid model name')
    root = (VARIANTS / 'cache').resolve()
    target = root / stem
    if target.resolve().parent != root:
        raise ValueError('Invalid model cache path')
    return target


def available_variant(source):
    """Do not offer partial files or artifacts derived from a replaced PLY."""
    try:
        directory = variant_directory(source.name)
        result = json.loads((directory / 'fast.json').read_text(encoding='utf8'))
        target = directory / 'fast.spz'
        if (result.get('state') == 'ready' and result.get('source') == source_signature(source)
                and target.is_file() and target.stat().st_size == result.get('bytes')):
            return result
    except (OSError, ValueError, TypeError):
        pass
    return None


def spz_header(path):
    with gzip.open(path, 'rb') as stream:
        magic, version, count, degree, bits, flags, reserved = struct.unpack('<IIIBBBB', stream.read(16))
        if magic != 0x5053474E or version != 3 or flags & ~1 or reserved or degree > 3:
            raise ValueError('Unexpected SPZ format')
        actual = 0
        for chunk in iter(lambda: stream.read(8*1024*1024), b''):
            actual += len(chunk)
        if actual != count * (20 + 3*((degree+1)**2-1)):
            raise ValueError('Incomplete SPZ payload')
    return {'splats': count, 'sh_degree': degree, 'fractional_bits': bits}


def ply_info(path):
    import numpy as np
    from plyfile import PlyData
    vertices = PlyData.read(path, mmap='r')['vertex'].data
    maximum = 0.0
    for name in ('x', 'y', 'z'):
        values = vertices[name]
        if not np.isfinite(values).all():
            raise ValueError('PLY contains non-finite positions')
        maximum = max(maximum, float(np.max(np.abs(values), initial=0)))
    rest = sum(name.startswith('f_rest_') for name in vertices.dtype.names)
    degree = {0: 0, 9: 1, 24: 2, 45: 3}.get(rest)
    if degree is None or not len(vertices):
        raise ValueError('Unsupported or empty Gaussian PLY')
    # Signed 24-bit fixed point: lower precision only when required by the range.
    bits = next((b for b in range(12, -1, -1) if maximum * 2**b < 2**23 - 1), None)
    if bits is None:
        raise ValueError('Coordinates exceed SPZ range')
    return len(vertices), degree, bits


def build_fast_variant(source, identifier=None):
    source = Path(source).resolve()
    with _lock:  # serialize the large WASM conversions, including different models
        cached = available_variant(source)
        if cached:
            return register_variant(source, cached, identifier)
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node.js is required to create fast model downloads')
        directory = variant_directory(source.name)
        directory.mkdir(parents=True, exist_ok=True)
        record = compression_record(identifier) if identifier else None
        record = record or new_record(source, identifier)
        record.update(state='running', started_at=utc_now())
        write_record_json(compression_directory(record['id']) / 'record.json', record)
        started = time.monotonic()
        temporary = directory / (uuid.uuid4().hex + '.partial')
        try:
            signature = source_signature(source)
            count, degree, bits = ply_info(source)
            source_hash = sha256_file(source)
            command = [node, '--max-old-space-size=8192', '--loader',
                       (ROOT / 'scripts/spark_node_loader.mjs').as_uri(),
                       str(ROOT / 'scripts/encode_model_spz.mjs'), str(source), str(temporary), str(bits)]
            run = subprocess.run(command, cwd=ROOT, text=True, encoding='utf8',
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
            if run.returncode:
                raise RuntimeError(run.stderr[-3000:])
            header = spz_header(temporary)
            if header['splats'] != count or header['sh_degree'] != degree:
                raise ValueError('Compression changed the Gaussian count or SH degree')
            if signature != source_signature(source) or source_hash != sha256_file(source):
                raise ValueError('Source changed during compression')
            if temporary.stat().st_size >= signature['bytes']:
                raise ValueError('Compressed model is not smaller than the full model')
            result = {'state': 'ready', 'model': source.name, 'format': 'spz',
                      'encoder': 'Spark 2.3.1', 'version': 3, 'source': signature,
                      'source_sha256': source_hash, 'sha256': sha256_file(temporary),
                      'bytes': temporary.stat().st_size, **header,
                      'duration_seconds': round(time.monotonic()-started, 3),
                      'limits': 'Quantized download; all Gaussians and SH degrees retained. GPU cost is unchanged.'}
            temporary.replace(directory / 'fast.spz')
            write_record_json(directory / 'fast.json', result)
            return register_variant(source, result, record['id'])
        except Exception as error:
            record.update(state='error', error=str(error), finished_at=utc_now())
            write_record_json(compression_directory(record['id']) / 'record.json', record)
            raise
        finally:
            temporary.unlink(missing_ok=True)


def start_fast_variant(source):
    source = Path(source).resolve()
    with _queue_lock:
        cached = available_variant(source)
        if cached and cached.get('record_id'):
            return cached['record_id']
        key = str(source)
        if key in _pending:
            return _pending[key]
        record = new_record(source)
        identifier = record['id']
        _pending[key] = identifier
        write_record_json(compression_directory(identifier) / 'record.json', record)
    def run():
        try:
            build_fast_variant(source, identifier)
        except Exception as error:
            record.update(state='error', error=str(error), finished_at=utc_now())
            write_record_json(compression_directory(identifier) / 'record.json', record)
            print(f'Fast model conversion failed for {Path(source).name}: {error}', flush=True)
        finally:
            with _queue_lock:
                _pending.pop(key, None)
    threading.Thread(target=run, daemon=True).start()
    return identifier


def rename_parent(old_name, new_source):
    with _lock:
        old = variant_directory(old_name)
        target = variant_directory(new_source.name)
        if old.exists() and not target.exists():
            old.rename(target)
        for record in compression_records():
            if record.get('parent_model') == old_name:
                record['parent_model'] = new_source.name
                write_record_json(compression_directory(record['id']) / 'record.json', record)


def delete_parent_variants(name):
    with _lock:
        directory = variant_directory(name)
        for filename in ('fast.spz', 'fast.json'):
            (directory / filename).unlink(missing_ok=True)
        for record in compression_records():
            if record.get('parent_model') == name:
                (compression_directory(record['id']) / 'model.spz').unlink(missing_ok=True)
                (compression_directory(record['id']) / 'glb_v1' / 'model.glb').unlink(missing_ok=True)
                for package in (compression_directory(record['id']) / 'glb_v1' / 'packages').glob('*/bundle.zip'):
                    package.unlink(missing_ok=True)
                record.update(state='deleted', deleted_at=utc_now())
                write_record_json(compression_directory(record['id']) / 'record.json', record)
