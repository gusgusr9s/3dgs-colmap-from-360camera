"""Small, dependency-free scene metadata shared by GLB and ZIP imports."""
import json
import math
import struct
import uuid

MAX_OBJECTS = 128
MAX_COLLIDERS = 512


def number(value, label, low=-1e12, high=1e12):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}: ค่าตัวเลขไม่ถูกต้อง')
    return value


def vector(value, label, length=3):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f'{label}: จำนวนแกนไม่ถูกต้อง')
    return [number(item, label) for item in value]


def name(value, label='ชื่อ', maximum=80):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f'{label} ต้องมี 1–{maximum} ตัวอักษร')
    return value.strip()


def normalize_scene(document):
    if not isinstance(document, dict) or document.get('version', 1) != 1:
        raise ValueError('รูปแบบฉากไม่รองรับ')
    objects, colliders = document.get('objects', []), document.get('colliders', [])
    if not isinstance(objects, list) or not isinstance(colliders, list) or len(objects) > MAX_OBJECTS or len(colliders) > MAX_COLLIDERS:
        raise ValueError('จำนวนโมเดลหรือกล่องตรวจชนเกินขีดจำกัด')
    identifiers = set()
    def identifier(item):
        value = item.get('id')
        if not isinstance(value, str) or not 1 <= len(value) <= 100 or value in identifiers:
            raise ValueError('ID ในฉากซ้ำหรือไม่ถูกต้อง')
        identifiers.add(value)
        return value
    normalized = []
    for item in objects:
        asset = item['asset']
        if asset['kind'] not in ('ply', 'spz', 'glb', 'splat') or not isinstance(asset['ref'], str) or not 1 <= len(asset['ref']) <= 512:
            raise ValueError('ไฟล์อ้างอิงในฉากไม่ถูกต้อง')
        normalized.append({'id': identifier(item), 'name': name(item.get('name', 'Model'), maximum=160),
                           'asset': {'kind': asset['kind'], 'ref': asset['ref']},
                           'position': vector(item.get('position', [0,0,0]), 'ตำแหน่งโมเดล'),
                           'rotation': vector(item.get('rotation', [0,0,0]), 'มุมโมเดล'),
                           'scale': number(item.get('scale', 1), 'สเกล', 1e-6, 1e6),
                           'visible': item.get('visible', True) is True})
    owners = {item['id'] for item in normalized}
    boxes = []
    for item in colliders:
        if item.get('kind') not in ('floor', 'wall') or (item.get('parent') is not None and item['parent'] not in owners):
            raise ValueError('กล่องตรวจชนอ้างอิงโมเดลไม่ถูกต้อง')
        size = vector(item['size'], 'ขนาดกล่อง')
        if min(size) <= 0:
            raise ValueError('ขนาดกล่องต้องมากกว่า 0')
        boxes.append({'id': identifier(item), 'name': name(item.get('name', 'Collider'), maximum=160),
                      'kind': item['kind'], 'parent': item.get('parent'),
                      'position': vector(item['position'], 'ตำแหน่งกล่อง'), 'size': size,
                      'rotation': vector(item.get('rotation', [0,item.get('yaw',0),0]), 'มุมกล่อง'),
                      'enabled': item.get('enabled', True) is True})
    spawn, view, player = document.get('spawn', {}), document.get('view', {}), document.get('player', {})
    return {'version': 1, 'name': name(document.get('name', 'Scene'), maximum=160),
            'units': 'meters' if document.get('units') == 'meters' else 'relative',
            'objects': normalized, 'colliders': boxes,
            'spawn': {'position': vector(spawn.get('position', [0,1.65,0]), 'จุดเริ่มเดิน'),
                      'yaw': number(spawn.get('yaw', 0), 'ทิศเริ่มเดิน')},
            'view': {'position': vector(view.get('position', [6,4,6]), 'มุมเริ่มต้น'),
                     'target': vector(view.get('target', [0,0,0]), 'จุดมอง')},
            'player': {'height': number(player.get('height', 1.65), 'ความสูง', .1, 100),
                       'radius': number(player.get('radius', .25), 'รัศมี', .01, 50),
                       'speed': number(player.get('speed', 1.4), 'ความเร็ว', .01, 100)}}


def summary(metadata):
    scene = metadata.get('scene')
    return {**{k: metadata[k] for k in ('points', 'vertices', 'faces', 'representation', 'sh_degree', 'spz_version', 'object_count') if k in metadata},
            'has_scene': scene is not None,
            'collider_count': sum(box['enabled'] for box in scene['colliders']) if scene else 0,
            'units': scene['units'] if scene else None}


def normalize_navigation(value):
    """Optional open sidecar/extras schema; models themselves need no metadata."""
    if not isinstance(value,dict) or not any(key in value for key in ('spawn','colliders','view')):raise ValueError('ข้อมูล navigation ไม่ถูกต้อง')
    boxes=[]
    for index,item in enumerate(value.get('colliders',[])):
        if item.get('type','box')!='box':continue
        boxes.append({**item,'id':item.get('id',f'box-{index}'),
                      'position':item.get('position',item.get('center',[0,0,0])),
                      'kind':item.get('kind','wall')})
    return normalize_scene({**value,'name':value.get('name','Navigation'),'objects':[],'colliders':boxes})


def inspect_glb(path, max_json=16*1024*1024, allow_external=False):
    with path.open('rb') as stream:
        if path.suffix.lower()=='.gltf':
            if path.stat().st_size>max_json:raise ValueError('glTF JSON ใหญ่เกินไป')
            return inspect_gltf_document(json.load(stream),allow_external)
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError('ไฟล์ GLB ไม่ครบ')
        magic, version, length, json_length, kind = struct.unpack('<4sIII4s', header)
        if magic != b'glTF' or version != 2 or length != path.stat().st_size or kind != b'JSON' or json_length > min(max_json, length-20):
            raise ValueError('ไฟล์ GLB ไม่ถูกต้อง')
        payload = json.loads(stream.read(json_length))
        if json_length%4:raise ValueError('GLB JSON chunk ต้องมีขนาดเป็นจำนวนเท่าของ 4')
        binary=False
        while stream.tell()<length:
            chunk=stream.read(8)
            if len(chunk)!=8:raise ValueError('GLB chunk ไม่ครบ')
            size,kind=struct.unpack('<I4s',chunk)
            if size%4 or size>length-stream.tell():raise ValueError('GLB chunk มีขนาดไม่ถูกต้อง')
            if kind==b'BIN\0':
                if binary:raise ValueError('GLB มี BIN chunk ซ้ำ')
                binary=True
            stream.seek(size,1)
    return inspect_gltf_document(payload,allow_external)


def inspect_gltf_document(payload,allow_external=False):
    if not isinstance(payload, dict) or payload.get('asset', {}).get('version') != '2.0':
        raise ValueError('ไฟล์ GLB ไม่ถูกต้อง')
    unsupported = set(payload.get('extensionsRequired', [])) & {'KHR_draco_mesh_compression','EXT_meshopt_compression','KHR_texture_basisu'}
    if unsupported:
        raise ValueError('GLB นี้ใช้ codec ที่ยังไม่รองรับ: ' + ', '.join(sorted(unsupported)))
    for item in payload.get('buffers', []) + payload.get('images', []):
        uri = item.get('uri')
        if uri and not uri.startswith('data:') and not allow_external:
            raise ValueError('ไฟล์มี buffer/texture ภายนอก กรุณารวมไฟล์ประกอบเป็น ZIP')
    scenes = payload.get('scenes', [])
    definition = scenes[payload.get('scene', 0)] if scenes else {}
    extra=definition.get('extras', {})
    embedded = extra.get('scene_document')
    scene = None
    if isinstance(embedded,dict) and any(key in embedded for key in ('objects','colliders','spawn','view')):
        try:scene=normalize_scene(embedded)
        except (ValueError,TypeError,KeyError,AttributeError):pass
    navigation=extra.get('navigation',payload.get('extras',{}).get('navigation'))
    if navigation is not None:
        try:scene=normalize_navigation(navigation)
        except (ValueError,TypeError,KeyError,AttributeError):pass
    nodes, meshes, accessors = payload.get('nodes', []), payload.get('meshes', []), payload.get('accessors', [])
    points, visits = 0, 0
    def visit(index, ancestors):
        nonlocal points, visits
        visits+=1
        if visits>200000:raise ValueError('โครงสร้าง glTF ซับซ้อนเกินไป')
        if index in ancestors or len(ancestors) > 128:
            raise ValueError('โครงสร้าง node ของ GLB วนซ้ำหรือลึกเกินไป')
        node = nodes[index]
        if 'mesh' in node:
            for primitive in meshes[node['mesh']]['primitives']:
                if 'KHR_gaussian_splatting' in primitive.get('extensions', {}):
                    points += number(accessors[primitive['attributes']['POSITION']]['count'],'จำนวนจุด',0,100000000)
        for child in node.get('children', []):
            visit(child, ancestors | {index})
    for root in definition.get('nodes', []):
        visit(root, set())
    return {'scene': scene, 'points': points or None,
            'object_count': len(scene['objects']) if scene else len(definition.get('nodes', []))}


def normalize_viewpoints(items):
    if not isinstance(items, list) or len(items) > 32:
        raise ValueError('บันทึกจุดนำทางได้สูงสุด 32 จุดต่อโมเดล')
    result, used = [], set()
    for item in items:
        identifier = item.get('id', uuid.uuid4().hex)
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier) or identifier in used:
            raise ValueError('ID จุดนำทางไม่ถูกต้องหรือซ้ำ')
        used.add(identifier)
        mode = item.get('mode', 'orbit')
        if mode not in ('orbit', 'walk', 'free'):
            raise ValueError('โหมดจุดนำทางไม่ถูกต้อง')
        quaternion = vector(item['quaternion'], 'ทิศมอง', 4)
        norm = math.hypot(*quaternion)
        if norm < 1e-8:
            raise ValueError('ทิศมองไม่ถูกต้อง')
        result.append({'id': identifier, 'name': name(item['name']), 'mode': mode,
                       'position': vector(item['position'], 'ตำแหน่ง'), 'target': vector(item['target'], 'จุดมอง'),
                       'quaternion': [v/norm for v in quaternion],
                       'fov': number(item.get('fov', 65), 'FOV', 10, 120)})
    return result
