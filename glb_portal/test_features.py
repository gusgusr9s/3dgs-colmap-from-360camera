"""Interoperability and persistence checks independent of the main application."""
from io import BytesIO
import json
from pathlib import Path
import struct
import os
import time
import zipfile
from unittest.mock import patch
from portal_api import cleanup_staging

import server
from test_server import PortalHTTPFixture
from portal_test_fixtures import (mesh_glb,mesh_ply,obj_mesh,gltf_files,zip_bytes,
                                  scene_zip,png_bytes,legacy_spz)


class FeatureTest(PortalHTTPFixture):
    def admin(self):
        _,cookie,_=self.login('admin','correct horse battery')
        csrf=json.loads(self.call('/api/me',headers={'Cookie':cookie})[2])['csrf']
        return {'Cookie':cookie,'X-CSRF-Token':csrf}

    def json_call(self,path,method,data,headers):
        return self.call(path,method,json.dumps(data).encode(),{**headers,'Content-Type':'application/json'})

    def upload(self,extension,content,headers):
        status,_,body=self.call('/api/models','POST',content,{**headers,'X-Filename':'model.'+extension})
        self.assertEqual(status,201,body)
        return json.loads(body)['id']

    def stage(self,content,headers):
        status,_,body=self.call('/api/imports','POST',content,{**headers,'X-Filename':'arbitrary.zip'})
        self.assertEqual(status,201,body)
        return json.loads(body)

    def commit(self,staged,headers):
        status,_,body=self.json_call('/api/imports/'+staged['id']+'/commit','POST',{'name':'Imported scene','project_id':'default'},headers)
        self.assertEqual(status,201,body)
        return json.loads(body)['id']

    def test_standard_mesh_and_point_files_without_scene_metadata(self):
        headers=self.admin()
        inputs=[('ply',mesh_ply(encoding),True) for encoding in ('ascii','binary_little_endian','binary_big_endian')]
        inputs += [('ply',mesh_ply('ascii',False),False),('obj',obj_mesh(),True),('gltf',gltf_files(True)['model/scene.gltf'],True)]
        inputs += [('spz',legacy_spz(version),False) for version in (1,2)]
        for extension,data,mesh in inputs:
            with self.subTest(extension=extension,prefix=data[:40]):
                identifier=self.upload(extension,data,headers)
                details=json.loads(self.call(f'/api/models/{identifier}/details',headers=headers)[2])
                self.assertIsNone(details['scene'])
                self.assertFalse(details['metadata']['has_scene'])
                if extension=='ply':self.assertEqual(details['metadata']['representation'],'mesh' if mesh else 'points')

    def test_plain_gltf_zip_preserves_external_buffers_and_textures(self):
        headers=self.admin();files=gltf_files();staged=self.stage(zip_bytes(files),headers)
        self.assertEqual(staged['metadata']['object_count'],1)
        self.assertEqual(json.loads(self.call('/api/models',headers=headers)[2])['models'],[])
        identifier=self.commit(staged,headers)
        details=json.loads(self.call(f'/api/models/{identifier}/details',headers=headers)[2])
        self.assertEqual(details['composition'][0]['position'],[0,0,0])
        self.assertIsNone(details['scene'])
        self.assertEqual(details['assets'][0]['path'],'model/scene.gltf')
        for filename,data in files.items():
            url=f'/api/models/{identifier}/assets/{filename}'
            self.assertEqual(self.call(url)[0],401)
            self.assertEqual(self.call(url,headers=headers)[2],data)
        self.assertEqual(self.call(f'/api/models/{identifier}/assets/unlisted.bin',headers=headers)[0],404)
        self.assertEqual(self.call(f'/api/models/{identifier}/assets/%2e%2e/portal.sqlite3',headers=headers)[0],400)
        self.assertEqual(self.call('/api/imports/'+staged['id']+'/commit','POST',b'{}',headers)[0],404)

    def test_obj_bundle_accepts_mtl_and_texture_without_custom_manifest(self):
        headers=self.admin()
        files={'model.obj':obj_mesh(True),'material.mtl':b'newmtl painted\nKd 1 1 1\nmap_Kd textures/paint.png\n','textures/paint.png':png_bytes()}
        identifier=self.commit(self.stage(zip_bytes(files),headers),headers)
        details=json.loads(self.call(f'/api/models/{identifier}/details',headers=headers)[2])
        self.assertEqual(details['assets'][0]['material_libraries'],['material.mtl'])
        for filename in files:self.assertEqual(self.call(f'/api/models/{identifier}/assets/{filename}',headers=headers)[0],200)

    def test_optional_scene_zip_adapter_preserves_spawn_and_colliders(self):
        headers=self.admin();staged=self.stage(scene_zip(),headers)
        self.assertEqual(staged['metadata']['collider_count'],2)
        identifier=self.commit(staged,headers)
        details=json.loads(self.call(f'/api/models/{identifier}/details',headers=headers)[2])
        self.assertEqual(details['scene']['spawn']['position'],[0,1.65,1])
        self.assertEqual(details['composition'][0]['position'],[1,0,0])
        self.assertEqual(self.call(f'/api/models/{identifier}/assets/serve.py',headers=headers)[0],404)
        self.assertFalse((server.DATA/'scenes'/identifier/'assets'/'serve.py').exists())

    def test_invalid_zip_paths_references_and_checksums_leave_no_staging_data(self):
        headers=self.admin();external=gltf_files();document=json.loads(external['model/scene.gltf']);document['buffers'][0]['uri']='https://example.com/private.bin';external['model/scene.gltf']=json.dumps(document).encode()
        missing=gltf_files();missing.pop('model/buffer.bin')
        mismatch=BytesIO(scene_zip())
        with zipfile.ZipFile(mismatch) as archive:bad_checksum={info.filename:archive.read(info) for info in archive.infolist()}
        bad_checksum['manifest.json']=json.dumps({'files':{'models/cloud.ply':{'sha256':'0'*64,'bytes':1}}}).encode()
        cases=[zip_bytes({'../escape.obj':obj_mesh()}),zip_bytes(external),zip_bytes(missing),zip_bytes(bad_checksum),b'invalid zip bytes'*5]
        for data in cases:
            with self.subTest(prefix=data[:25]):
                self.assertEqual(self.call('/api/imports','POST',data,headers)[0],400)
                self.assertEqual(list((server.DATA/'imports').iterdir()),[])

    def test_thumbnail_roles_png_validation_and_cleanup(self):
        headers=self.admin();identifier=self.upload('glb',mesh_glb(),headers);url=f'/api/models/{identifier}/thumbnail'
        self.assertEqual(self.call(url,'POST',png_bytes(),headers)[0],200)
        self.assertEqual(self.call(url)[0],401)
        self.assertEqual(self.call(url,headers=headers)[2],png_bytes())
        self.assertEqual(self.call(url,'POST',png_bytes()[:-2],headers)[0],400)
        _,guest,_=self.login('guest','another long password')
        self.assertEqual(self.call(url,'POST',png_bytes(),{'Cookie':guest})[0],403)
        self.assertTrue(json.loads(self.call('/api/models',headers=headers)[2])['models'][0]['thumbnail'])
        self.assertEqual(self.call(f'/api/models/{identifier}','DELETE',headers=headers)[0],200)
        self.assertFalse((server.DATA/'thumbnails'/(identifier+'.png')).exists())

    def test_viewpoints_work_for_generic_glb_and_detect_conflicts(self):
        headers=self.admin();identifier=self.upload('glb',mesh_glb(),headers);url=f'/api/models/{identifier}/viewpoints'
        view={'id':'a'*32,'name':'Entrance','mode':'walk','position':[0,1.65,0],'target':[0,1.65,-1],'quaternion':[0,0,0,1],'fov':65}
        self.assertEqual(self.json_call(url,'PATCH',{'viewpoints':[view],'revision':0},headers)[0],200)
        self.assertEqual(self.json_call(url,'PATCH',{'viewpoints':[view],'revision':0},headers)[0],409)
        details=json.loads(self.call(f'/api/models/{identifier}/details',headers=headers)[2]);self.assertEqual(details['viewpoints'][0]['name'],'Entrance')
        self.assertEqual(details['viewpoints_revision'],1)
        invalid={**view,'position':[0,float('nan'),0]}
        self.assertEqual(self.json_call(url,'PATCH',{'viewpoints':[invalid],'revision':1},headers)[0],400)
        _,guest,_=self.login('guest','another long password')
        self.assertEqual(self.json_call(url,'PATCH',{'viewpoints':[],'revision':1},{'Cookie':guest})[0],403)

    def test_bulk_move_is_atomic_and_project_rename_is_shared(self):
        headers=self.admin();ids=[self.upload('glb',mesh_glb(),headers) for _ in range(3)]
        status,_,body=self.json_call('/api/projects','POST',{'name':'Destination'},headers);self.assertEqual(status,201);project=json.loads(body)['id']
        self.assertEqual(self.json_call('/api/models/batch','POST',{'ids':[ids[0],'f'*32],'action':'move','project_id':project},headers)[0],404)
        self.assertEqual(len(json.loads(self.call('/api/models',headers=headers)[2])['models']),3)
        self.assertEqual(self.json_call('/api/models/batch','POST',{'ids':ids[:2],'action':'move','project_id':project},headers)[0],200)
        self.assertEqual(len(json.loads(self.call('/api/models?project='+project,headers=headers)[2])['models']),2)
        self.assertEqual(self.json_call('/api/projects/'+project,'PATCH',{'name':'Changed name'},headers)[0],200)
        self.assertEqual(self.json_call('/api/projects/'+project,'PATCH',{'name':'ทั่วไป'},headers)[0],409)
        self.assertEqual(self.json_call('/api/models/batch','POST',{'ids':ids[:2],'action':'delete'},headers)[0],200)
        for identifier in ids[:2]:self.assertFalse((server.DATA/'models'/(identifier+'.glb')).exists())

    def test_cancel_staged_import_and_collisions_in_glb(self):
        headers=self.admin();staged=self.stage(scene_zip(),headers)
        self.assertEqual(self.call('/api/imports/'+staged['id'],'DELETE',headers=headers)[0],200)
        self.assertEqual(list((server.DATA/'imports').iterdir()),[])
        identifier=self.upload('glb',mesh_glb(True),headers)
        details=json.loads(self.call(f'/api/models/{identifier}/details',headers=headers)[2])
        self.assertEqual(details['metadata']['collider_count'],2)
        self.assertEqual(details['scene']['player']['speed'],1.4)

    def test_packaged_collision_helpers_match_shared_source(self):
        for filename in ('scene_collision.mjs','scene_geometry.mjs'):
            self.assertEqual((server.ROOT/filename).read_text(encoding='utf8'),(server.ROOT.parent/'assets'/'app'/filename).read_text(encoding='utf8'))

    def test_unknown_extras_do_not_lock_standard_files_to_a_schema(self):
        headers=self.admin();content=mesh_glb();length=int.from_bytes(content[12:16],'little');document=json.loads(content[20:20+length]);binary=content[28+length:]
        document['scenes'][0]['extras']={'scene_document':{'unrelated':'metadata'}}
        from portal_test_fixtures import encode_glb
        identifier=self.upload('glb',encode_glb(document,binary),headers)
        self.assertIsNone(json.loads(self.call(f'/api/models/{identifier}/details',headers=headers)[2])['scene'])
        content=zip_bytes({'model.glb':mesh_glb(),'scene.json':b'not a portal scene descriptor'})
        staged=self.stage(content,headers);self.assertEqual(staged['metadata']['object_count'],1)

    def test_generic_navigation_sidecar_and_staging_expiry(self):
        headers=self.admin()
        navigation={'spawn':{'position':[1,2,3]},'colliders':[{'type':'box','kind':'floor','center':[0,0,0],'size':[10,.2,10]}]}
        staged=self.stage(zip_bytes({'model.glb':mesh_glb(),'navigation.json':json.dumps(navigation).encode()}),headers)
        self.assertEqual(staged['metadata']['collider_count'],1)
        folder=server.DATA/'imports'/staged['id'];past=time.time()-90000;os.utime(folder,(past,past));cleanup_staging(server.DATA)
        self.assertFalse(folder.exists())
