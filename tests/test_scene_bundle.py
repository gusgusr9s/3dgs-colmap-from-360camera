"""GLB integrity, attached collider persistence and portable scene ZIPs."""
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from app.models import scene_assets, scene_package, scene_projects
from app.web.scene_ui import collision_obj


def cube_glb(external=False):
    positions=[(-.4,0,-.4),(.4,0,-.4),(.4,.8,-.4),(-.4,.8,-.4),(-.4,0,.4),(.4,0,.4),(.4,.8,.4),(-.4,.8,.4)]
    faces=[0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,0,4,7,0,7,3,1,2,6,1,6,5]
    binary=b''.join(struct.pack('<3f',*p) for p in positions)+struct.pack('<36H',*faces)
    document={'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
      'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1,'material':0}]}],
      'materials':[{'pbrMetallicRoughness':{'baseColorFactor':[.25,.85,.2,1],'metallicFactor':0,'roughnessFactor':.7}}],
      'buffers':[{'byteLength':len(binary)}],'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':96,'target':34962},{'buffer':0,'byteOffset':96,'byteLength':72,'target':34963}],
      'accessors':[{'bufferView':0,'componentType':5126,'count':8,'type':'VEC3','min':[-.4,0,-.4],'max':[.4,.8,.4]},{'bufferView':1,'componentType':5123,'count':36,'type':'SCALAR'}]}
    if external:document['images']=[{'uri':'https://example.invalid/texture.png'}]
    text=json.dumps(document).encode();text+=b' '*((-len(text))%4)
    return struct.pack('<5I',0x46546c67,2,28+len(text)+len(binary),len(text),0x4e4f534a)+text+struct.pack('<2I',len(binary),0x004e4942)+binary


class SceneBundleTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='scene_bundle_');self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        for module,attribute,folder in [(scene_assets,'ASSETS','assets'),(scene_package,'PACKAGES','packages'),(scene_projects,'SCENES','scenes')]:
            context=patch.object(module,attribute,self.root/folder);context.start();self.addCleanup(context.stop)

    def test_glb_validation_no_partial_files_and_external_dependencies(self):
        data=cube_glb();record=scene_assets.import_glb(io.BytesIO(data),len(data),'../green box.glb')
        self.assertEqual(record['label'],'green box.glb');self.assertEqual(scene_assets.asset_path(record['id']).read_bytes(),data)
        self.assertEqual(len(scene_assets.records()),1)
        for blob in [b'not glb',data[:-4],cube_glb(True)]:
            with self.assertRaises((ValueError,UnicodeError)):
                scene_assets.import_glb(io.BytesIO(blob),len(blob),'bad.glb')
        with self.assertRaises(ValueError):scene_assets.import_glb(io.BytesIO(data[:40]),len(data),'partial.glb')
        self.assertEqual(len(scene_assets.records()),1);self.assertFalse(list(scene_assets.ASSETS.glob('*.partial')))
        for value in ['../outside','x/y','a'*31]:
            with self.assertRaises(ValueError):scene_assets.asset_path(value)

    def test_parent_rotation_scale_obj_and_save_reopen(self):
        data=cube_glb();asset=scene_assets.import_glb(io.BytesIO(data),len(data),'cube.glb')
        doc=scene_projects.create_scene({'objects':[{'id':'owner','asset':{'kind':'glb','ref':asset['id']},'position':[10,0,0],'rotation':[0,1.5707963267948966,0],'scale':2}],
          'colliders':[{'parent':'owner','kind':'wall','position':[0,1,-1],'size':[4,2,.2]}]},lambda kind,ref:scene_assets.record(ref))
        reopened=scene_projects.read_scene(doc['id']);self.assertEqual(reopened,doc)
        vertices=[list(map(float,line.split()[1:])) for line in collision_obj(doc).decode().splitlines() if line.startswith('v ')]
        self.assertAlmostEqual(sum(p[0] for p in vertices)/8,8)
        self.assertAlmostEqual(max(p[2] for p in vertices)-min(p[2] for p in vertices),8)
        self.assertAlmostEqual(max(p[1] for p in vertices)-min(p[1] for p in vertices),4)
        # Local thickness may be smaller than .001 when its parent is scaled up.
        doc['objects'][0]['scale']=1000
        doc['colliders'][0]['size'][1]=.0002
        doc=scene_projects.save_scene(doc['id'],doc,lambda kind,ref:True)
        self.assertEqual(scene_projects.read_scene(doc['id'])['colliders'][0]['size'][1],.0002)
        doc['colliders'][0]['parent']='missing'
        with self.assertRaises(ValueError):scene_projects.save_scene(doc['id'],doc,lambda kind,ref:True)

    def test_real_bundle_integrity_sources_and_cache(self):
        data=cube_glb();asset=scene_assets.import_glb(io.BytesIO(data),len(data),'cube.glb')
        ply=self.root/'map.ply';ply.write_bytes(b'ply\nformat ascii 1.0\nelement vertex 0\nend_header\n')
        doc=scene_projects.create_scene({'objects':[{'asset':{'kind':'ply','ref':'map.ply'}},{'asset':{'kind':'glb','ref':asset['id']}}]},lambda kind,ref:True)
        resolver=lambda kind,ref:ply if kind=='ply' else scene_assets.asset_path(ref)
        state=scene_package.start(doc,resolver)
        again=scene_package.start(doc,resolver);self.assertEqual(again['key'],state['key'])
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            state=scene_package.status(doc['id'])
            if state['state'] in {'ready','error'}:break
            time.sleep(.02)
        self.assertEqual(state['state'],'ready',state)
        target=scene_package.download_path(doc['id'],state['key'])
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip());manifest=json.loads(archive.read('manifest.json'))
            for name,info in manifest['files'].items():
                blob=archive.read(name);self.assertEqual(len(blob),info['bytes']);self.assertEqual(hashlib.sha256(blob).hexdigest(),info['sha256'])
            contents=json.loads(archive.read('scene.json'));self.assertEqual(contents['scene'],doc)
            self.assertEqual(archive.read('models/model-1.glb'),data)
            self.assertIn('assets/scene_player.mjs',archive.namelist());self.assertIn('vendor/three-0.180.0/examples/jsm/loaders/GLTFLoader.js',archive.namelist())
            self.assertNotIn('https://',archive.read('index.html').decode())
        self.assertEqual(scene_package.start(doc,resolver)['state'],'ready')
        for key in ['../outside','x'*32]:
            with self.assertRaises(ValueError):scene_package.download_path(doc['id'],key)


if __name__=='__main__':unittest.main()
