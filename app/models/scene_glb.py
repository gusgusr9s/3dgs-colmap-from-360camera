"""Stream a scene into one GLB, preserving Gaussian attributes and node transforms."""
import base64
import copy
import json
from pathlib import Path
import struct

import numpy as np
from plyfile import PlyData

from app.models.scene_geometry import rotation_matrix, world_box

GAUSSIAN = 'KHR_gaussian_splatting'
CORE_ARRAYS = ('bufferViews','accessors','images','samplers','textures','materials','meshes','cameras','skins','nodes','animations')
SUPPORTED_EXTENSIONS = {
    GAUSSIAN, 'KHR_lights_punctual','EXT_mesh_gpu_instancing','KHR_mesh_quantization',
    'KHR_texture_transform','EXT_texture_webp','EXT_texture_avif','KHR_materials_unlit',
    'KHR_materials_clearcoat','KHR_materials_transmission','KHR_materials_volume',
    'KHR_materials_ior','KHR_materials_specular','KHR_materials_sheen','KHR_materials_iridescence',
    'KHR_materials_anisotropy','KHR_materials_emissive_strength','KHR_materials_dispersion',
}


def glb_info(path):
    path=Path(path);size=path.stat().st_size
    with path.open('rb') as stream:
        header=stream.read(20)
        if len(header)!=20:raise ValueError('Incomplete GLB')
        magic,version,length,json_length,kind=struct.unpack('<5I',header)
        if (magic,version,length,kind)!=(0x46546c67,2,size,0x4e4f534a) or json_length>min(size-20,32*1024*1024):
            raise ValueError('Invalid GLB header')
        document=json.loads(stream.read(json_length))
        binary=None
        while stream.tell()<size:
            chunk=stream.read(8)
            if len(chunk)!=8:raise ValueError('Incomplete GLB chunk')
            count,kind=struct.unpack('<2I',chunk)
            if count>size-stream.tell():raise ValueError('Incomplete GLB binary')
            if kind==0x004e4942:
                if binary is not None:raise ValueError('Multiple GLB binary chunks')
                binary=(path,stream.tell(),count)
            stream.seek(count,1)
    if document.get('asset',{}).get('version')!='2.0':raise ValueError('Expected glTF 2.0')
    return document,binary


def _header(document,binary_length):
    encoded=json.dumps(document,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')
    encoded+=b' '*((-len(encoded))%4)
    length=28+len(encoded)+binary_length
    if length>0xffffffff:raise ValueError('ฉากเกินขีดจำกัด GLB 4 GB กรุณาลดจำนวนโมเดลหรือแบ่งฉาก')
    return struct.pack('<5I',0x46546c67,2,length,len(encoded),0x4e4f534a)+encoded+struct.pack('<2I',binary_length,0x004e4942)


def ply_to_glb(source,target,progress=lambda value:None):
    """Preserve full PLY precision; no SPZ re-encoding or SH truncation."""
    vertices=PlyData.read(str(source),mmap='r')['vertex'].data;count=len(vertices)
    names=set(vertices.dtype.names or ())
    required={'x','y','z','opacity',*[f'rot_{k}' for k in range(4)],*[f'scale_{k}' for k in range(3)],*[f'f_dc_{k}' for k in range(3)]}
    if not count or not required<=names:raise ValueError('PLY ต้องมีข้อมูล Gaussian ครบ')
    rest=sum(name.startswith('f_rest_') for name in names)
    degree=next((d for d in range(4) if 3*((d+1)**2-1)==rest),None)
    if degree is None or not {f'f_rest_{k}' for k in range(rest)}<=names:raise ValueError('PLY SH coefficients ไม่ครบ')
    coefficients=(degree+1)**2;stride=(11+coefficients*3)*4
    attributes={};accessors=[];offset=0
    def attribute(name,kind,components):
        nonlocal offset
        attributes[name]=len(accessors);accessors.append({'bufferView':0,'byteOffset':offset,'componentType':5126,'count':count,'type':kind});offset+=components*4
    attribute('POSITION','VEC3',3);attribute(GAUSSIAN+':ROTATION','VEC4',4);attribute(GAUSSIAN+':SCALE','VEC3',3);attribute(GAUSSIAN+':OPACITY','SCALAR',1)
    for band in range(degree+1):
        for coefficient in range(2*band+1):attribute(f'{GAUSSIAN}:SH_DEGREE_{band}_COEF_{coefficient}','VEC3',3)
    lower=np.full(3,np.inf);upper=-lower
    for start in range(0,count,8192):
        chunk=vertices[start:start+8192];positions=np.column_stack([chunk[k] for k in ('x','y','z')])
        if not np.isfinite(positions).all():raise ValueError('PLY contains non-finite positions')
        lower=np.minimum(lower,positions.min(axis=0));upper=np.maximum(upper,positions.max(axis=0))
    accessors[0].update(min=lower.tolist(),max=upper.tolist())
    document={'asset':{'version':'2.0','generator':'360 Studio Gaussian PLY export'},'extensionsUsed':[GAUSSIAN],'extensionsRequired':[GAUSSIAN],
      'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
      'meshes':[{'primitives':[{'mode':0,'attributes':attributes,'extensions':{GAUSSIAN:{'kernel':'ellipse','colorSpace':'srgb_rec709_display','projection':'perspective','sortingMethod':'cameraDistance'}}}]}],
      'buffers':[{'byteLength':count*stride}],'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':count*stride,'byteStride':stride,'target':34962}],'accessors':accessors}
    with Path(target).open('xb') as stream:
        stream.write(_header(document,count*stride))
        for start in range(0,count,8192):
            chunk=vertices[start:start+8192];values=np.empty((len(chunk),stride//4),dtype='<f4')
            values[:,:3]=np.column_stack([chunk[k] for k in ('x','y','z')])
            q=np.column_stack([chunk[f'rot_{k}'] for k in (1,2,3,0)]).astype(float);norm=np.linalg.norm(q,axis=1)
            if (norm<1e-12).any():raise ValueError('PLY quaternion is zero')
            values[:,3:7]=q/norm[:,None];values[:,7:10]=np.exp(np.column_stack([chunk[f'scale_{k}'] for k in range(3)]).astype(float))
            values[:,10]=1/(1+np.exp(-np.clip(chunk['opacity'].astype(float),-80,80)))
            values[:,11:14]=np.column_stack([chunk[f'f_dc_{k}'] for k in range(3)])
            if rest:values[:,14:]=np.column_stack([chunk[f'f_rest_{k}'] for k in range(rest)]).reshape(len(chunk),3,-1).transpose(0,2,1).reshape(len(chunk),-1)
            if not np.isfinite(values).all():raise ValueError('PLY contains non-finite Gaussian attributes')
            stream.write(values.tobytes());progress(int(100*(start+len(chunk))/count))
    return {'splats':count,'sh_degree':degree}


def node_matrix(position,rotation,scale):
    matrix=np.eye(4);matrix[:3,:3]=np.asarray(rotation_matrix(rotation))*np.asarray(scale);matrix[:3,3]=position
    return matrix.T.ravel().tolist()


class Composer:
    def __init__(self,scene):
        self.scene=scene;self.segments=[];self.length=0;self.sources={};self.used=set();self.required=set()
        self.document={'asset':{'version':'2.0','generator':'360 Studio scene export'},'scene':0,
                       'scenes':[{'name':scene['name'],'nodes':[],'extras':{'scene_document':copy.deepcopy(scene)}},{'name':'Collision boxes','nodes':[]}],
                       **{name:[] for name in CORE_ARRAYS}}
        view=scene['view'];eye=np.asarray(view['position'],dtype=float);back=eye-np.asarray(view['target']);length=np.linalg.norm(back)
        if length>1e-8:
            back/=length;up=np.array([0.,1.,0.]);right=np.cross(up,back)
            if np.linalg.norm(right)<1e-8:right=np.cross(np.array([0.,0.,1.]),back)
            right/=np.linalg.norm(right);up=np.cross(back,right);matrix=np.eye(4);matrix[:3,:3]=np.column_stack([right,up,back]);matrix[:3,3]=eye
            self.document['scenes'][0]['extras']['initial_view']={'transform_matrix':matrix.tolist(),'fov_y':55}

    def segment(self,value):
        padding=(-self.length)%4
        if padding:self.segments.append(bytes(padding));self.length+=padding
        offset=self.length;self.segments.append(value);self.length+=len(value) if isinstance(value,bytes) else value[2]
        return offset

    def buffers(self,document,path,binary):
        key=str(Path(path).resolve())
        if key in self.sources:return self.sources[key]
        bases=[]
        for index,buffer in enumerate(document.get('buffers',[])):
            uri=buffer.get('uri')
            if uri:
                if not uri.startswith('data:') or ';base64,' not in uri:raise ValueError('GLB references an external buffer')
                data=base64.b64decode(uri.split(',',1)[1],validate=True)
                if len(data)!=buffer['byteLength']:raise ValueError('GLB buffer length mismatch')
                bases.append(self.segment(data))
            else:
                if index!=0 or binary is None or not 0<=binary[2]-buffer['byteLength']<=3:raise ValueError('GLB binary length mismatch')
                bases.append(self.segment((binary[0],binary[1],buffer['byteLength'])))
        self.sources[key]=bases;return bases

    def add(self,path,item):
        source,binary=glb_info(path);extensions=set(source.get('extensionsUsed',[]))|set(source.get('extensionsRequired',[]))
        if extensions-SUPPORTED_EXTENSIONS:raise ValueError('GLB extension ยังส่งออกรวมไม่ได้: '+', '.join(sorted(extensions-SUPPORTED_EXTENSIONS)))
        self.used.update(extensions);self.required.update(source.get('extensionsRequired',[]))
        bases=self.buffers(source,path,binary);offsets={name:len(self.document[name]) for name in CORE_ARRAYS};doc=copy.deepcopy(source)
        def remap(record,key,array):
            if key in record:record[key]+=offsets[array]
        for view in doc.get('bufferViews',[]):
            index=view.get('buffer',0);buffer=source['buffers'][index]
            if view.get('byteOffset',0)<0 or view['byteLength']<0 or view.get('byteOffset',0)+view['byteLength']>buffer['byteLength']:raise ValueError('GLB bufferView outside buffer')
            view['byteOffset']=view.get('byteOffset',0)+bases[index];view['buffer']=0
        for accessor in doc.get('accessors',[]):
            remap(accessor,'bufferView','bufferViews')
            for record in accessor.get('sparse',{}).values():
                if isinstance(record,dict):remap(record,'bufferView','bufferViews')
        for image in doc.get('images',[]):
            remap(image,'bufferView','bufferViews')
            if image.get('uri') and not image['uri'].startswith('data:'):raise ValueError('GLB references an external image')
        for texture in doc.get('textures',[]):
            remap(texture,'sampler','samplers');remap(texture,'source','images')
            for value in texture.get('extensions',{}).values():remap(value,'source','images')
        def textures(value):
            if isinstance(value,dict):
                for name,child in value.items():
                    if name=='extras':continue
                    if name.endswith('Texture') and isinstance(child,dict):remap(child,'index','textures')
                    textures(child)
            elif isinstance(value,list):
                for child in value:textures(child)
        for material in doc.get('materials',[]):textures(material)
        for mesh in doc.get('meshes',[]):
            for primitive in mesh['primitives']:
                remap(primitive,'indices','accessors');remap(primitive,'material','materials')
                primitive['attributes']={name:index+offsets['accessors'] for name,index in primitive['attributes'].items()}
                for target in primitive.get('targets',[]):
                    for name in target:target[name]+=offsets['accessors']
        for skin in doc.get('skins',[]):
            remap(skin,'inverseBindMatrices','accessors');remap(skin,'skeleton','nodes');skin['joints']=[index+offsets['nodes'] for index in skin['joints']]
        lights=doc.get('extensions',{}).get('KHR_lights_punctual',{}).get('lights',[])
        output_lights=self.document.setdefault('extensions',{}).setdefault('KHR_lights_punctual',{'lights':[]})['lights'] if lights else []
        light_offset=len(output_lights);output_lights.extend(lights)
        for node in doc.get('nodes',[]):
            for key,array in [('mesh','meshes'),('skin','skins'),('camera','cameras')]:remap(node,key,array)
            if 'children' in node:node['children']=[index+offsets['nodes'] for index in node['children']]
            ext=node.get('extensions',{})
            if 'KHR_lights_punctual' in ext:ext['KHR_lights_punctual']['light']+=light_offset
            if 'EXT_mesh_gpu_instancing' in ext:
                attributes=ext['EXT_mesh_gpu_instancing']['attributes'];ext['EXT_mesh_gpu_instancing']['attributes']={name:index+offsets['accessors'] for name,index in attributes.items()}
        for animation in doc.get('animations',[]):
            for sampler in animation['samplers']:remap(sampler,'input','accessors');remap(sampler,'output','accessors')
            for channel in animation['channels']:remap(channel['target'],'node','nodes')
        for name in CORE_ARRAYS:self.document[name].extend(doc.get(name,[]))
        roots=source.get('scenes',[{'nodes':[]}])[source.get('scene',0)].get('nodes',[])
        parent=len(self.document['nodes']);self.document['nodes'].append({'name':item['name'],'matrix':node_matrix(item['position'],item['rotation'],[item['scale']]*3),
              'children':[index+offsets['nodes'] for index in roots],'extras':{'scene_object_id':item['id'],'source_asset':item['asset']}})
        self.document['scenes'][0]['nodes'].append(parent)

    def collision(self):
        corners=[(-.5,-.5,-.5),(.5,-.5,-.5),(.5,.5,-.5),(-.5,.5,-.5),(-.5,-.5,.5),(.5,-.5,.5),(.5,.5,.5),(-.5,.5,.5)]
        faces=[0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,0,4,7,0,7,3,1,2,6,1,6,5]
        boxes=[box for box in self.scene['colliders'] if box['enabled']]
        if not boxes:return
        offset=self.segment(b''.join(struct.pack('<3f',*corner) for corner in corners)+struct.pack('<36H',*faces))
        vi=len(self.document['bufferViews']);self.document['bufferViews'].extend([{'buffer':0,'byteOffset':offset,'byteLength':96,'target':34962},{'buffer':0,'byteOffset':offset+96,'byteLength':72,'target':34963}])
        ai=len(self.document['accessors']);self.document['accessors'].extend([{'bufferView':vi,'componentType':5126,'count':8,'type':'VEC3','min':[-.5]*3,'max':[.5]*3},{'bufferView':vi+1,'componentType':5123,'count':36,'type':'SCALAR'}])
        mi=len(self.document['meshes']);self.document['meshes'].append({'name':'Collision unit box','primitives':[{'attributes':{'POSITION':ai},'indices':ai+1}]})
        for box in boxes:
            position,rotation,size=world_box(box,self.scene['objects']);matrix=np.eye(4);matrix[:3,:3]=np.asarray(rotation)*np.asarray(size);matrix[:3,3]=position
            node=len(self.document['nodes']);self.document['nodes'].append({'name':box['name'],'mesh':mi,'matrix':matrix.T.ravel().tolist(),'extras':{'collision':True,'collider_id':box['id']}});self.document['scenes'][1]['nodes'].append(node)

    def write(self,target,progress=lambda value:None):
        padding=(-self.length)%4
        if padding:self.segments.append(bytes(padding));self.length+=padding
        self.document['buffers']=[{'byteLength':self.length}]
        if self.used:self.document['extensionsUsed']=sorted(self.used)
        if self.required:self.document['extensionsRequired']=sorted(self.required)
        document={key:value for key,value in self.document.items() if key not in CORE_ARRAYS or value}
        if not document['scenes'][1]['nodes']:document['scenes']=document['scenes'][:1]
        with Path(target).open('xb') as output:
            output.write(_header(document,self.length));copied=0
            for segment in self.segments:
                if isinstance(segment,bytes):output.write(segment);copied+=len(segment)
                else:
                    path,offset,length=segment
                    with Path(path).open('rb') as source:
                        source.seek(offset)
                        while length:
                            data=source.read(min(length,1024*1024))
                            if not data:raise ValueError('GLB source was truncated')
                            output.write(data);copied+=len(data);length-=len(data);progress(int(100*copied/max(1,self.length)))
        return {'bytes':Path(target).stat().st_size,'models':len(self.document['scenes'][0]['nodes']),'collision_boxes':sum(box['enabled'] for box in self.scene['colliders'])}
