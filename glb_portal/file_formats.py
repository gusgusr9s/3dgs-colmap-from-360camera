"""Validation for the uncompressed Gaussian formats supported by Spark."""
import math
import struct


FORMATS = ("glb", "gltf", "obj", "spz", "ply", "splat", "zip")
GAUSSIAN_FIELDS = {"x", "y", "z", "opacity", *(f"scale_{i}" for i in range(3)),
                   *(f"rot_{i}" for i in range(4)), *(f"f_dc_{i}" for i in range(3))}
MAX_POINTS = 100_000_000


def validate_ply(path):
    """Accept standard, float32, little-endian 3DGS PLY, never mesh/ASCII PLY."""
    with path.open("rb") as stream:
        lines = []
        header_size = 0
        while header_size < 65536:
            line = stream.readline(4096)
            header_size += len(line)
            if not line or len(line) == 4096:
                raise ValueError("ส่วนหัว PLY ไม่ถูกต้อง")
            lines.append(line.decode("ascii").strip())
            if lines[-1] == "end_header":
                break
        else:
            raise ValueError("ส่วนหัว PLY ใหญ่เกินไป")
        if lines[:2] != ["ply", "format binary_little_endian 1.0"]:
            raise ValueError("รองรับ PLY แบบ Gaussian Splat binary_little_endian เท่านั้น")
        count, fields, element = None, [], None
        for line in lines[2:-1]:
            parts = line.split()
            if not parts or parts[0] in ("comment", "obj_info"):
                continue
            if parts[0] == "element" and len(parts) == 3:
                element = parts[1]
                size = int(parts[2])
                if element == "vertex" and count is None:
                    count = size
                elif size != 0:
                    raise ValueError("PLY ต้องเป็น Gaussian Splat ไม่มี face หรือ element อื่น")
            elif parts[0] == "property" and element == "vertex":
                if len(parts) != 3 or parts[1] not in ("float", "float32") or parts[2] in fields:
                    raise ValueError("รองรับ property ของ PLY แบบ float32 เท่านั้น")
                fields.append(parts[2])
            else:
                raise ValueError("โครงสร้าง PLY ไม่รองรับ")
        if not count or not 0 < count <= MAX_POINTS or not GAUSSIAN_FIELDS.issubset(fields):
            raise ValueError("PLY ต้องมีตำแหน่ง สี opacity scale และ rotation ของ Gaussian Splat")
        if path.stat().st_size != stream.tell() + count * len(fields) * 4:
            raise ValueError("ข้อมูล PLY ไม่ครบหรือขนาดไม่ตรงกับส่วนหัว")
        # Bound working memory independently of the uploaded file size.
        while block := stream.read(1024 * 1024):
            if any(not math.isfinite(value[0]) for value in struct.iter_unpack("<f", block)):
                raise ValueError("PLY มีค่าตัวเลขที่ไม่ถูกต้อง")
        return {'points': count, 'representation': 'gaussian'}


def validate_splat(path):
    """Raw .splat: position/scale float32, RGBA uint8, quaternion uint8."""
    size = path.stat().st_size
    if not size or size % 32 or size // 32 > MAX_POINTS:
        raise ValueError("SPLAT ต้องเป็นข้อมูล Gaussian ขนาด 32 bytes ต่อจุด")
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            for row in struct.iter_unpack("<6f8B", block):
                if (any(not math.isfinite(value) for value in row[:6])
                        or any(value <= 0 for value in row[3:6])
                        or all(value == 128 for value in row[10:14])):
                    raise ValueError("SPLAT มีตำแหน่ง scale หรือ rotation ไม่ถูกต้อง")
    return {'points': size//32, 'representation': 'gaussian'}


PLY_TYPES = {'char':'b','int8':'b','uchar':'B','uint8':'B','short':'h','int16':'h',
             'ushort':'H','uint16':'H','int':'i','int32':'i','uint':'I','uint32':'I',
             'float':'f','float32':'f','double':'d','float64':'d'}


def inspect_ply(path):
    """Accept ordinary ASCII/binary PLY meshes/points as well as 3DGS PLY."""
    with path.open('rb') as stream:
        elements, fields, encoding, header_bytes = [], [], None, 0
        if stream.readline(8).strip() != b'ply': raise ValueError('ไฟล์ PLY ไม่ถูกต้อง')
        while True:
            line = stream.readline(4096);header_bytes += len(line)
            if not line or len(line) == 4096 or header_bytes > 65536: raise ValueError('ส่วนหัว PLY ไม่ถูกต้อง')
            tokens = line.decode('ascii').strip().split()
            if not tokens or tokens[0] in ('comment','obj_info'): continue
            if tokens == ['end_header']: break
            if tokens[0] == 'format' and len(tokens) == 3 and tokens[2] == '1.0':
                encoding = tokens[1]
            elif tokens[0] == 'element' and len(tokens) == 3:
                count = int(tokens[2])
                if not 0 <= count <= MAX_POINTS: raise ValueError('จำนวนข้อมูล PLY ไม่ถูกต้อง')
                elements.append({'name':tokens[1], 'count':count, 'properties':[]})
            elif tokens[0] == 'property' and elements:
                if len(tokens) == 3 and tokens[1] in PLY_TYPES: prop = (tokens[2], tokens[1], None)
                elif len(tokens) == 5 and tokens[1] == 'list' and tokens[2] in PLY_TYPES and tokens[3] in PLY_TYPES:
                    if PLY_TYPES[tokens[2]] in ('f','d'): raise ValueError('ชนิด list ของ PLY ไม่ถูกต้อง')
                    prop = (tokens[4], tokens[3], tokens[2])
                else: raise ValueError('ชนิด property ของ PLY ไม่รองรับ')
                elements[-1]['properties'].append(prop)
                if elements[-1]['name'] == 'vertex': fields.append(prop[0])
            else: raise ValueError('ส่วนหัว PLY ไม่ถูกต้อง')
        if encoding not in ('ascii','binary_little_endian','binary_big_endian') or not {'x','y','z'} <= set(fields):
            raise ValueError('PLY ต้องมีตำแหน่ง vertex x y z')
        if any(field.startswith(('f_dc_','scale_','rot_')) for field in fields):
            return validate_ply(path)
        vertices = next((element['count'] for element in elements if element['name']=='vertex'),0)
        faces = sum(element['count'] for element in elements if element['name']=='face')
        if not vertices: raise ValueError('PLY ไม่มีจุด')
        endian = '<' if encoding == 'binary_little_endian' else '>'
        def binary_value(kind):
            fmt=endian+PLY_TYPES[kind];size=struct.calcsize(fmt);data=stream.read(size)
            if len(data)!=size:raise ValueError('ข้อมูล PLY ไม่ครบ')
            return struct.unpack(fmt,data)[0]
        for element in elements:
            if element['count'] and not element['properties']:raise ValueError('PLY ไม่มี property')
            for _ in range(element['count']):
                values = None
                if encoding=='ascii':
                    row=stream.readline(1024*1024)
                    if not row or len(row)==1024*1024:raise ValueError('ข้อมูล PLY ไม่ครบหรือแถวยาวเกินไป')
                    values=iter(row.decode('ascii').split())
                def take(kind):
                    if values is None:return binary_value(kind)
                    token=next(values)
                    return float(token) if PLY_TYPES[kind] in ('f','d') else int(token)
                try:
                    for field,kind,list_kind in element['properties']:
                        length=take(list_kind) if list_kind else 1
                        if not 0<=length<=100000:raise ValueError('PLY list ยาวเกินไป')
                        for _ in range(length):
                            value=take(kind)
                            if not math.isfinite(value):raise ValueError('PLY มีค่าตัวเลขไม่ถูกต้อง')
                            if element['name']=='face' and field in ('vertex_indices','vertex_index') and not 0<=value<vertices:
                                raise ValueError('PLY face อ้างอิง vertex ไม่ถูกต้อง')
                    if values is not None and next(values,None) is not None:raise ValueError('ข้อมูล PLY เกินจำนวน property')
                except StopIteration:raise ValueError('ข้อมูล PLY ไม่ครบ') from None
        while trailing:=stream.read(1024*1024):
            if encoding!='ascii' or trailing.strip():raise ValueError('ข้อมูล PLY เกินส่วนหัว')
        return {'points': None if faces else vertices, 'vertices':vertices, 'faces':faces,
                'representation':'mesh' if faces else 'points'}


def inspect_obj(path):
    vertices=faces=0;libraries=[]
    with path.open('r',encoding='utf-8-sig') as stream:
        while line:=stream.readline(1024*1024):
            if len(line)==1024*1024:raise ValueError('แถว OBJ ยาวเกินไป')
            tokens=line.split('#',1)[0].split()
            if not tokens:continue
            if tokens[0] in ('v','vn','vt'):
                values=[float(value) for value in tokens[1:]]
                if len(values)<(2 if tokens[0]=='vt' else 3) or any(not math.isfinite(v) for v in values):raise ValueError('ตำแหน่ง OBJ ไม่ถูกต้อง')
                if tokens[0]=='v':vertices+=1
            elif tokens[0] in ('f','l','p'):
                if len(tokens)<(4 if tokens[0]=='f' else 2):raise ValueError('ข้อมูล OBJ ไม่ครบ')
                for token in tokens[1:]:
                    index=int(token.split('/')[0])
                    if index==0 or abs(index)>vertices:raise ValueError('OBJ อ้างอิง vertex ไม่ถูกต้อง')
                if tokens[0]=='f':faces+=1
            elif tokens[0]=='mtllib':libraries.append(line.split('#',1)[0].split(None,1)[1].strip())
    if not vertices:raise ValueError('OBJ ไม่มี vertex')
    return {'vertices':vertices,'faces':faces,'object_count':1,'representation':'mesh','material_libraries':libraries}
