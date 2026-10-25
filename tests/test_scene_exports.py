"""Numerical export checks for Gaussian attributes, mixed scenes and background jobs."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from plyfile import PlyData, PlyElement

from app.models import scene_exports,scene_projects
from app.models.scene_glb import Composer,glb_info,ply_to_glb,node_matrix
from scripts.scene_test_server import fixture_glb


def gaussian_ply(path,degree=3):
    names=['x','y','z','opacity']+[f'rot_{k}' for k in range(4)]+[f'scale_{k}' for k in range(3)]+[f'f_dc_{k}' for k in range(3)]+[f'f_rest_{k}' for k in range(3*((degree+1)**2-1))]
    vertices=np.zeros(4,dtype=[(name,'<f4') for name in names]);vertices['x']=[-2,-1,1,2];vertices['y']=1;vertices['z']=3;vertices['rot_0']=np.cos(.2);vertices['rot_2']=np.sin(.2);vertices['opacity']=.7
    for k in range(3):vertices[f'scale_{k}']=-k-1;vertices[f'f_dc_{k}']=k*.2
    for k in range(3*((degree+1)**2-1)):vertices[f'f_rest_{k}']=k*.01
    PlyData([PlyElement.describe(vertices,'vertex')],text=False).write(str(path))
    return vertices


def gaussian_spz(path):
    count=4;positions=b''.join(int(value*4096).to_bytes(3,'little',signed=True) for x in [-2,-1,1,2] for value in [x,1,3])
    data=struct.pack('<IIIBBBB',0x5053474e,3,count,1,12,0,0)+positions+b'\xff'*count+b'\x80'*(count*3)+b'\x80'*(count*3)+struct.pack('<I',3<<30)*count+b'\x80'*(count*9)
    path.write_bytes(gzip.compress(data))


def accessor_values(path,document,index):
    accessor=document['accessors'][index];view=document['bufferViews'][accessor['bufferView']];_,binary=glb_info(path)
    components={'SCALAR':1,'VEC3':3,'VEC4':4}[accessor['type']];dtype={5126:'<f4',5123:'<u2'}[accessor['componentType']]
    with path.open('rb') as stream:
        stream.seek(binary[1]+view.get('byteOffset',0)+accessor.get('byteOffset',0));data=stream.read(view['byteLength']-accessor.get('byteOffset',0))
    return np.ndarray((accessor['count'],components),dtype=dtype,buffer=data,strides=(view.get('byteStride',components*np.dtype(dtype).itemsize),np.dtype(dtype).itemsize))


class SceneExportTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='scene_export_');self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        for module,attribute,folder in [(scene_exports,'EXPORTS','exports'),(scene_projects,'SCENES','scenes')]:
            context=patch.object(module,attribute,self.root/folder);context.start();self.addCleanup(context.stop)

    def wait(self,scene):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            state=scene_exports.status(scene['id'])
            if state['state'] in {'ready','error'} and scene['id'] not in scene_exports._active:return state
            time.sleep(.02)
        self.fail('Scene export did not finish')

    def test_full_precision_ply_all_sh_degrees_and_invalid_values(self):
        for degree in range(4):
            source=self.root/f'input-{degree}.ply';original=gaussian_ply(source,degree);digest=hashlib.sha256(source.read_bytes()).hexdigest();target=self.root/f'out-{degree}.glb'
            result=ply_to_glb(source,target);document,_=glb_info(target);attributes=document['meshes'][0]['primitives'][0]['attributes']
            self.assertEqual(result['splats'],4);self.assertEqual(result['sh_degree'],degree)
            np.testing.assert_array_equal(accessor_values(target,document,attributes['POSITION'])[:,0],original['x'])
            np.testing.assert_allclose(accessor_values(target,document,attributes['KHR_gaussian_splatting:ROTATION'])[0],[0,np.sin(.2),0,np.cos(.2)],atol=1e-7)
            np.testing.assert_allclose(accessor_values(target,document,attributes['KHR_gaussian_splatting:SCALE'])[0],np.exp([-1,-2,-3]),atol=1e-7)
            self.assertAlmostEqual(float(accessor_values(target,document,attributes['KHR_gaussian_splatting:OPACITY'])[0,0]),1/(1+np.exp(-.7)),places=6)
            if degree:
                rest=(degree+1)**2-1
                np.testing.assert_allclose(accessor_values(target,document,attributes['KHR_gaussian_splatting:SH_DEGREE_1_COEF_0'])[0],[original[f'f_rest_{k*rest}'][0] for k in range(3)],atol=1e-7)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)
        source=self.root/'bad.ply';values=gaussian_ply(source);values['rot_0']=0;values['rot_2']=0;PlyData([PlyElement.describe(values,'vertex')],text=False).write(str(source))
        with self.assertRaises(ValueError):ply_to_glb(source,self.root/'bad.glb')

    def test_mixed_glb_remapping_instances_transforms_and_collision_scene(self):
        source=self.root/'map.ply';gaussian_ply(source);gaussian=self.root/'map.glb';ply_to_glb(source,gaussian)
        prop=self.root/'cube.glb';prop.write_bytes(fixture_glb())
        scene=scene_projects.create_scene({'objects':[
          {'id':'map','name':'Map','asset':{'kind':'ply','ref':'map.ply'},'position':[10,2,-3],'rotation':[.3,.7,-.1],'scale':2},
          {'id':'copy','asset':{'kind':'ply','ref':'map.ply'},'position':[-10,0,0]},
          {'id':'prop','asset':{'kind':'glb','ref':'cube'},'position':[2,3,4]}],
          'colliders':[{'parent':'map','name':'Attached floor','position':[0,-.1,0],'size':[8,.2,8]}]},lambda kind,ref:True)
        composer=Composer(scene)
        for item in scene['objects']:composer.add(prop if item['asset']['kind']=='glb' else gaussian,item)
        composer.collision();target=self.root/'scene.glb';result=composer.write(target);document,binary=glb_info(target)
        self.assertEqual(result['models'],3);self.assertEqual(result['collision_boxes'],1);self.assertEqual(document['scene'],0)
        self.assertEqual(len(document['scenes'][0]['nodes']),3);self.assertEqual(len(document['scenes'][1]['nodes']),1)
        nodes=document['nodes'];roots=document['scenes'][0]['nodes'];np.testing.assert_allclose(nodes[roots[0]]['matrix'],node_matrix([10,2,-3],[.3,.7,-.1],[2]*3))
        first=document['bufferViews'][0];second=document['bufferViews'][1]
        self.assertEqual(first['byteOffset'],second['byteOffset'],'duplicated models share binary data')
        cube=nodes[nodes[roots[2]]['children'][0]];primitive=document['meshes'][cube['mesh']]['primitives'][0]
        self.assertGreater(primitive['attributes']['POSITION'],0);self.assertEqual(primitive['material'],0)
        np.testing.assert_allclose(accessor_values(target,document,primitive['attributes']['POSITION'])[0],[-.4,0,-.4])
        for view in document['bufferViews']:self.assertLessEqual(view.get('byteOffset',0)+view['byteLength'],document['buffers'][0]['byteLength'])
        self.assertEqual(target.stat().st_size,binary[1]+binary[2])
        self.assertNotIn('images',document)

    @unittest.skipUnless(shutil.which('node'),'Node.js required for SPZ export')
    def test_background_spz_export_cache_retry_source_change_and_safe_paths(self):
        source=self.root/'map.spz';gaussian_spz(source);scene=scene_projects.create_scene({'objects':[{'asset':{'kind':'spz','ref':'map'}}]},lambda kind,ref:True)
        resolver=lambda kind,ref:source
        first=scene_exports.start(scene,resolver,lambda path:True);self.assertEqual(scene_exports.start(scene,resolver,lambda path:True)['key'],first['key'])
        state=self.wait(scene);self.assertEqual(state['state'],'ready',state);self.assertEqual(state['splats'],4)
        target=scene_exports.download_path(scene['id'],state['key']);document,_=glb_info(target)
        self.assertEqual(document['extensionsRequired'],['KHR_gaussian_splatting']);self.assertEqual(scene_exports.start(scene,resolver,lambda path:True)['state'],'ready')
        target.unlink();self.assertEqual(scene_exports.status(scene['id'])['state'],'error')
        scene_exports.start(scene,resolver,lambda path:True);self.assertEqual(self.wait(scene)['state'],'ready')
        # A source replaced while output is being written must never become ready.
        scene['objects'][0]['position'][0]=2
        original=Composer.write
        def changed(composer,*args,**kwargs):
            result=original(composer,*args,**kwargs);source.write_bytes(source.read_bytes()+b'changed');return result
        with patch.object(Composer,'write',changed):
            scene_exports.start(scene,resolver,lambda path:True);self.assertEqual(self.wait(scene)['state'],'error')
        self.assertFalse(list(scene_exports.directory(scene['id']).glob('*.partial')))
        self.assertFalse(list(scene_exports.directory(scene['id']).glob('convert_*')))
        for identifier,key in [('../outside','a'*32),('a'*32,'../outside')]:
            with self.assertRaises(ValueError):scene_exports.download_path(identifier,key)

    def test_textured_props_remap_images_and_preserve_custom_metadata(self):
        raw=fixture_glb();length=struct.unpack_from('<I',raw,12)[0];document=json.loads(raw[20:20+length]);binary=raw[28+length:]
        document['images']=[{'uri':'data:image/png;base64,iVBORw0KGgo='}]
        document['samplers']=[{'magFilter':9729}];document['textures']=[{'source':0,'sampler':0}]
        document['materials'][0]['pbrMetallicRoughness']['baseColorTexture']={'index':0}
        document['materials'][0]['extras']={'index':'keep this metadata'}
        encoded=json.dumps(document).encode();encoded+=b' '*((-len(encoded))%4)
        prop=self.root/'textured.glb';prop.write_bytes(struct.pack('<5I',0x46546c67,2,28+len(encoded)+len(binary),len(encoded),0x4e4f534a)+encoded+struct.pack('<2I',len(binary),0x004e4942)+binary)
        scene=scene_projects.create_scene({'objects':[{'asset':{'kind':'glb','ref':'texture'}},{'asset':{'kind':'glb','ref':'texture'},'position':[2,0,0]}]},lambda kind,ref:True)
        composer=Composer(scene)
        for item in scene['objects']:composer.add(prop,item)
        target=self.root/'textures.glb';composer.write(target);result,_=glb_info(target)
        self.assertEqual(result['textures'][1],{'source':1,'sampler':1})
        self.assertEqual(result['materials'][1]['pbrMetallicRoughness']['baseColorTexture']['index'],1)
        self.assertEqual(result['materials'][1]['extras']['index'],'keep this metadata')
        self.assertEqual(result['bufferViews'][0]['byteOffset'],result['bufferViews'][2]['byteOffset'])


if __name__=='__main__':unittest.main()
