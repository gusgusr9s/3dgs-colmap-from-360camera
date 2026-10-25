"""Validated self-contained GLB assets used by scene projects."""
import hashlib
import json
from pathlib import Path
import re
import struct
import tempfile
import uuid

from app.paths import WORKSPACE

ASSETS = WORKSPACE / 'web_data' / 'scene_assets'
MAX_BYTES = 256 * 1024 * 1024


def asset_path(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('Invalid GLB asset ID')
    root = Path(ASSETS).resolve()
    target = root / (identifier + '.glb')
    if target.resolve().parent != root:
        raise ValueError('Invalid GLB asset path')
    return target


def validate_glb(path):
    size = path.stat().st_size
    with path.open('rb') as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError('ไฟล์ GLB ไม่ครบ')
        magic, version, length, chunk_length, chunk_type = struct.unpack('<5I', header)
        if magic != 0x46546c67 or version != 2 or length != size or size > MAX_BYTES or chunk_type != 0x4e4f534a or chunk_length > min(size - 20, 16*1024*1024):
            raise ValueError('รองรับ GLB 2.0 ที่สมบูรณ์ ขนาดไม่เกิน 256 MB')
        document = json.loads(stream.read(chunk_length).decode('utf8'))
        if not isinstance(document, dict) or document.get('asset', {}).get('version') != '2.0' or not document.get('meshes'):
            raise ValueError('GLB ต้องมี mesh')
        for item in document.get('buffers', []) + document.get('images', []):
            uri = item.get('uri', '')
            if uri and (not isinstance(uri, str) or not uri.startswith('data:')):
                raise ValueError('GLB ต้องรวม textures และ buffers ในไฟล์ ไม่อ้าง URL หรือไฟล์ภายนอก')
        unavailable = set(document.get('extensionsRequired', [])) & {'KHR_draco_mesh_compression','KHR_texture_basisu','EXT_meshopt_compression','KHR_gaussian_splatting'}
        if unavailable:
            raise ValueError('โปรดส่งออก GLB mesh โดยไม่บีบอัด Draco/Meshopt/KTX2 สำหรับรุ่นนี้')
        # Verify every following chunk lies fully inside the declared file.
        while stream.tell() < size:
            chunk_header = stream.read(8)
            if len(chunk_header) != 8:
                raise ValueError('GLB chunk ไม่ครบ')
            chunk_size, _ = struct.unpack('<II', chunk_header)
            if chunk_size > size - stream.tell():
                raise ValueError('GLB binary chunk ไม่ครบ')
            stream.seek(chunk_size, 1)
    return document


def import_glb(stream, length, filename):
    if not isinstance(length, int) or not 20 <= length <= MAX_BYTES:
        raise ValueError('GLB ต้องมีขนาดไม่เกิน 256 MB')
    root = Path(ASSETS); root.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    destination = asset_path(identifier)
    temporary = None
    try:
        digest = hashlib.sha256()
        with tempfile.NamedTemporaryFile(dir=root, suffix='.partial', delete=False) as output:
            temporary = Path(output.name)
            remaining = length
            while remaining:
                data = stream.read(min(1024*1024, remaining))
                if not data:
                    raise ValueError('รับไฟล์ GLB ไม่ครบ กรุณาลองอีกครั้ง')
                output.write(data); digest.update(data); remaining -= len(data)
        validate_glb(temporary)
        label = Path(str(filename).replace('\\','/')).name[:140] or 'model.glb'
        label = ''.join(c for c in label if ord(c) >= 32)
        record = {'id':identifier,'kind':'glb','ref':identifier,'label':label,'bytes':length,'sha256':digest.hexdigest(),
                  'url':'/scene-asset/'+identifier+'.glb','world_up':[0,1,0]}
        temporary.replace(destination)
        try:
            destination.with_suffix('.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf8')
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return record
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def record(identifier):
    path = asset_path(identifier)
    data = json.loads(path.with_suffix('.json').read_text(encoding='utf8'))
    if data.get('id') != identifier or not path.is_file() or path.stat().st_size != data.get('bytes'):
        raise ValueError('ไม่พบ GLB ที่พร้อมใช้งาน')
    return data


def records():
    result = []
    for path in Path(ASSETS).glob('*.json'):
        try:
            result.append(record(path.stem))
        except (OSError, ValueError, TypeError):
            pass
    return sorted(result,key=lambda item:item['label'].casefold())
