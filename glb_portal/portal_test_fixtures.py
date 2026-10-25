"""Tiny deterministic assets for HTTP and real GPU browser checks."""
import json
import math
import struct
import base64
from io import BytesIO
import zipfile
import zlib
import gzip


def gaussian_ply():
    fields = ['x','y','z','f_dc_0','f_dc_1','f_dc_2','opacity',
              'scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
    points = []
    for y in range(20):
        for x in range(32):
            angle = x / 32 * math.tau
            points.append((math.cos(angle), (y-10)*.09, math.sin(angle),
                           .8, (y/20)*2-1, -.5, 3., -2.8, -2.8, -2.8, 1., 0., 0., 0.))
    header = 'ply\nformat binary_little_endian 1.0\nelement vertex %s\n' % len(points)
    header += ''.join(f'property float {field}\n' for field in fields) + 'end_header\n'
    return header.encode() + b''.join(struct.pack('<14f', *point) for point in points)


def raw_splat():
    return b''.join(struct.pack('<6f8B', math.cos(i/32*math.tau), (j-10)*.09,
                               math.sin(i/32*math.tau), .06, .06, .06,
                               100, 200, 140, 255, 255, 128, 128, 128)
                    for j in range(20) for i in range(32))


def legacy_spz(version=2):
    points=[(math.cos(i/16*math.tau),(j-4)*.15,math.sin(i/16*math.tau)) for j in range(8) for i in range(16)]
    positions=b''.join(struct.pack('<3e',*point) if version==1 else b''.join((round(value*4096)&0xffffff).to_bytes(3,'little') for value in point) for point in points)
    count=len(points);data=positions+bytes([255])*count+bytes([128,180,140])*count+bytes([115])*count*3+bytes([128])*count*3
    return gzip.compress(struct.pack('<IIIBBBB',0x5053474e,version,count,0,12,0,0)+data)


def encode_glb(payload,data):
    encoded=json.dumps(payload).encode();encoded+=b' '*(-len(encoded)%4)
    data+=b'\0'*(-len(data)%4)
    return (struct.pack('<4sII',b'glTF',2,28+len(encoded)+len(data))+
            struct.pack('<I4s',len(encoded),b'JSON')+encoded+struct.pack('<I4s',len(data),b'BIN\0')+data)


def mesh_glb(navigation=False,native_camera=False):
    # Lit, double-sided mesh: catches missing scene lighting in the portal.
    data = struct.pack('<9f', -1., -1., 0., 1., -1., 0., 0., 1., 0.)
    payload = {'asset': {'version':'2.0'}, 'scene':0, 'scenes':[{'nodes':[0,1]}],
               'nodes':[{'mesh':0,'translation':[-1.2,0,0]}, {'mesh':0,'translation':[1.2,0,-.5]}],
               'meshes':[{'primitives':[{'attributes':{'POSITION':0},'material':0}]}],
               'materials':[{'doubleSided':True,'pbrMetallicRoughness':{
                   'baseColorFactor':[.4,.8,.55,1],'metallicFactor':0,'roughnessFactor':.7}}],
               'buffers':[{'byteLength':len(data)}],
               'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':len(data)}],
               'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3',
                             'min':[-1,-1,0],'max':[1,1,0]}]}
    if navigation:payload['scenes'][0]['extras']={'scene_document':navigation_scene()}
    if native_camera:
        payload['cameras']=[{'name':'Entrance camera','type':'perspective','perspective':{'yfov':1.,'znear':.01}}]
        payload['nodes'].append({'name':'Entrance camera','camera':0,'translation':[3,2,4]});payload['scenes'][0]['nodes'].append(2)
    return encode_glb(payload,data)


def navigation_scene():
    return {'version':1,'name':'Generic navigation','units':'meters','objects':[],
            'colliders':[{'id':'floor','name':'Ground','kind':'floor','position':[0,-.1,0],'size':[10,.2,10]},
                         {'id':'wall','name':'Back wall','kind':'wall','position':[0,1.5,-1],'size':[10,3,.2]}],
            'spawn':{'position':[0,1.65,1],'yaw':0},'player':{'height':1.65,'radius':.25,'speed':1.4},
            'view':{'position':[4,3,5],'target':[0,0,0]}}


def mesh_ply(encoding='ascii',faces=True):
    header=f'ply\nformat {encoding} 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n'
    if faces:header+='element face 1\nproperty list uchar int vertex_indices\n'
    header+='end_header\n';positions=[(-1.,-1.,0.),(1.,-1.,0.),(0.,1.,0.)]
    if encoding=='ascii':body=''.join(' '.join(map(str,row))+'\n' for row in positions).encode()+(b'3 0 1 2\n' if faces else b'')
    else:
        endian='<' if encoding=='binary_little_endian' else '>'
        body=b''.join(struct.pack(endian+'3f',*row) for row in positions)+(struct.pack(endian+'B3i',3,0,1,2) if faces else b'')
    return header.encode()+body


def obj_mesh(material=False):
    return ((('mtllib material.mtl\nusemtl painted\n' if material else '')+
             'v -1 -1 0\nv 1 -1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt .5 1\nf 1/1 2/2 3/3\n').encode())


def png_bytes():
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'+b'\x55\xcc\x88'*2+b'\0'+b'\x55\xcc\x88'*2))+chunk(b'IEND',b'')


def gltf_files(inline=False):
    glb=mesh_glb();length=int.from_bytes(glb[12:16],'little');document=json.loads(glb[20:20+length]);binary=glb[28+length:]
    document['buffers'][0]['uri']='data:application/octet-stream;base64,'+base64.b64encode(binary).decode() if inline else 'buffer.bin'
    document['images']=[{'uri':'data:image/png;base64,'+base64.b64encode(png_bytes()).decode() if inline else '../textures/paint.png'}]
    document['textures']=[{'source':0}];document['materials'][0]['pbrMetallicRoughness']['baseColorTexture']={'index':0}
    document['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_0']=1
    # Texture coordinates in a separate buffer view.
    coordinates=struct.pack('<6f',0,0,1,0,.5,1);offset=len(binary);binary+=coordinates
    document['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':len(coordinates)})
    document['accessors'].append({'bufferView':1,'componentType':5126,'count':3,'type':'VEC2'})
    document['buffers'][0]['byteLength']=len(binary)
    if inline:document['buffers'][0]['uri']='data:application/octet-stream;base64,'+base64.b64encode(binary).decode()
    return {'model/scene.gltf':json.dumps(document).encode(),'model/buffer.bin':binary,'textures/paint.png':png_bytes()}


def zip_bytes(files):
    output=BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for filename,data in files.items():archive.writestr(filename,data)
    return output.getvalue()


def scene_zip():
    scene=navigation_scene();scene['objects']=[{'id':'model','name':'Gaussian model','asset':{'kind':'ply','ref':'arbitrary.ply'},'position':[1,0,0],'rotation':[0,0,0],'scale':1,'visible':True}]
    return zip_bytes({'scene.json':json.dumps({'scene':scene,'assets':[{'kind':'ply','ref':'arbitrary.ply','url':'./models/cloud.ply'}]}).encode(),'models/cloud.ply':gaussian_ply(),'serve.py':b'not executed'})
