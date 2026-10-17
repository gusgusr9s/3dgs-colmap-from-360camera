"""Spatial edit math, immutable full-property output, lineage, concurrency and HTTP."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uuid

import numpy as np
from plyfile import PlyData, PlyElement
import model_crop as crop
import web_app as app


class CropTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crop_test_')
        self.root = Path(self.temp.name)
        self.models = self.root/'models'; self.models.mkdir()
        self.patches = [patch.object(crop, 'EDITS', self.root/'edits')]
        for p in self.patches: p.start()
        self.addCleanup(self.temp.cleanup)
        for p in self.patches: self.addCleanup(p.stop)
        names = ['x','y','z','nx','ny','nz'] + [f'f_dc_{i}' for i in range(3)] + [f'f_rest_{i}' for i in range(45)]
        names += ['opacity']+[f'scale_{i}' for i in range(3)]+[f'rot_{i}' for i in range(4)]
        self.vertices = np.zeros(8, dtype=[(name,'<f4') for name in names])
        self.vertices['x'] = [0, .2, -.2, .8, 2, -2, 0, 0]
        self.vertices['y'] = [0, .2, -.2, .8, 0, 0, 2, -2]
        self.vertices['rot_0'] = 1; self.vertices['opacity'] = .5
        for k in range(3): self.vertices[f'scale_{k}'] = np.log(.01)
        for k in range(45): self.vertices[f'f_rest_{k}'] = np.arange(8)*.011 + k*.001
        self.source = self.models/'source.ply'; self.write_source()
        self.box = {'origin':[0,0,0], 'rotation':np.eye(3).tolist(), 'min':[-1,-1,-1], 'max':[1,1,1], 'strict':True}

    def write_source(self, byte_order='<', text=False):
        PlyData([PlyElement.describe(self.vertices, 'vertex')], byte_order=byte_order, text=text).write(self.source)

    def payload(self):
        mask = crop.select_vertices(self.vertices, self.box)
        return {'model':self.source.name, 'source_signature':crop.source_signature(self.source),
                'source_sha256':crop.sha256_file(self.source), 'crop':self.box,
                'selection_sha256':hashlib.sha256(mask.tobytes()).hexdigest(), 'request_id':uuid.uuid4().hex}

    def wait(self, identifier):
        limit = time.monotonic()+20
        while time.monotonic()<limit:
            state = crop.edit_status(identifier, app.task_lock_alive)
            if state and state['state'] in {'ready','error'}:
                with (crop.edit_directory(identifier)/'.lock').open('a+b') as handle:
                    if crop.try_lock(handle): return state
            time.sleep(.02)
        self.fail('Crop worker did not finish')

    def test_anisotropic_envelope_and_rotated_box(self):
        v = self.vertices[:1].copy()
        v['x'] = .8; v['scale_0']=np.log(.1)
        self.assertEqual(crop.select_vertices(v,self.box).tolist(),[0], 'a long Gaussian crosses the wall')
        self.assertEqual(crop.select_vertices(v,{**self.box,'strict':False}).tolist(),[1])
        # Rotate the long Gaussian away from X: its projected radius is now small enough.
        v['rot_0']=np.sqrt(.5); v['rot_3']=np.sqrt(.5)
        self.assertEqual(crop.select_vertices(v,self.box).tolist(),[1])
        # A rotated thin room: world X=0.8 becomes local Y=-0.8, outside its narrow Y range.
        turned={**self.box,'rotation':[[0,-1,0],[1,0,0],[0,0,1]],'min':[-1,-.2,-1],'max':[1,.2,1]}
        self.assertEqual(crop.select_vertices(v,turned).tolist(),[0])

    def test_preserves_all_properties_endian_ascii_and_parent(self):
        metadata = {'job':'original_job','run_id':'20261013_203220_964927f8','source_key':'raw:video.mp4',
                    'viewer_config':{'transform_matrix':np.eye(4).tolist()},'quality':{'psnr':30,'published_splats':8}}
        for order, text in [('<',False),('>',False),('<',True)]:
            with self.subTest(order=order,text=text):
                self.write_source(order,text); before=crop.sha256_file(self.source); request=self.payload()
                state=crop.start_edit(self.models,request,metadata,app.task_lock_alive)
                state=self.wait(state['id']); self.assertEqual(state['state'],'ready',state)
                output=self.models/state['model']
                actual=PlyData.read(output)['vertex'].data
                expected=self.vertices[crop.select_vertices(self.vertices,self.box).astype(bool)]
                self.assertEqual(actual.dtype.names,expected.dtype.names)
                for name in expected.dtype.names: np.testing.assert_array_equal(actual[name],expected[name])
                self.assertEqual(crop.sha256_file(self.source),before)
                meta=json.loads(output.with_suffix('.meta.json').read_text(encoding='utf8'))
                self.assertEqual(meta['quality'],{'published_splats':len(expected)})
                self.assertEqual(meta['parent_quality']['psnr'],30)
                self.assertEqual(meta['source_key'],metadata['source_key'])
                self.assertEqual(meta['edit']['parent_model'],self.source.name)
                self.assertNotEqual(output.name,self.source.name)
                self.assertEqual(crop.start_edit(self.models,request,metadata,app.task_lock_alive)['model'],output.name)
                with patch.object(app,'MODELS',self.models):
                    with self.assertRaisesRegex(ValueError,'checkpoint'): app.refinement_input(output.name)

    def test_chunked_save_and_idempotence_while_queued(self):
        self.vertices=np.tile(self.vertices,9000); self.write_source()
        request=self.payload()
        # Another request must reuse this queued operation, even before its worker starts writing.
        gate=threading.Lock(); gate.acquire()
        original=crop.select_vertices
        def delayed(*args,**kwargs):
            with gate: return original(*args,**kwargs)
        with patch.object(crop,'select_vertices',new=delayed):
            state=crop.start_edit(self.models,request,{},app.task_lock_alive)
            again=crop.start_edit(self.models,request,{},app.task_lock_alive)
            self.assertEqual(state['id'],again['id'])
            gate.release(); state=self.wait(state['id'])
        self.assertEqual(state['state'],'ready',state)
        self.assertEqual(len(list(self.models.glob('*crop*.ply'))),1)
        actual=PlyData.read(self.models/state['model'])['vertex'].data
        expected=self.vertices[original(self.vertices,self.box).astype(bool)]
        np.testing.assert_array_equal(actual,expected)

    def test_invalid_bounds_paths_empty_stale_and_interrupted(self):
        bad=[{**self.box,'min':[2,0,0]}, {**self.box,'max':[float('nan'),1,1]},
             {**self.box,'rotation':[[2,0,0],[0,1,0],[0,0,1]]}, {**self.box,'rotation':np.diag([-1,1,1]).tolist()},
             {**self.box,'strict':'false'}]
        for value in bad:
            with self.assertRaises(ValueError): crop.validate_crop(value)
        for name in ['../source.ply','x\\y.ply','/absolute.ply','source.glb']:
            with self.assertRaises(ValueError): crop.model_path(self.models,name)
        request=self.payload(); self.vertices['x'][0]=.1; self.write_source()
        with self.assertRaises(ValueError): crop.preview(self.models,request)
        request=self.payload(); request['selection_sha256']='0'*64
        state=crop.start_edit(self.models,request,{},app.task_lock_alive);state=self.wait(state['id'])
        self.assertEqual(state['state'],'error');self.assertEqual(len(list(self.models.glob('*crop*.ply'))),0)
        self.box.update(min=[10,10,10],max=[11,11,11]);request=self.payload()
        state=crop.start_edit(self.models,request,{},app.task_lock_alive);state=self.wait(state['id'])
        self.assertEqual(state['state'],'error');self.assertFalse(list(self.models.glob('*.partial')))
        identifier=uuid.uuid4().hex;directory=crop.edit_directory(identifier);directory.mkdir(parents=True)
        crop.write_status(directory,{'id':identifier,'state':'running','pid':os.getpid()})
        self.assertEqual(crop.edit_status(identifier,app.task_lock_alive)['state'],'error')

    def test_preview_http_and_new_model_identity(self):
        with patch.object(app,'MODELS',self.models):
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base+'/model-edit/source.ply') as response:
                    page=response.read().decode();self.assertIn('crop-save',page);self.assertNotIn('__CONFIG__',page)
                with urlopen(base+'/api/model-crop/info?model=source.ply') as response: info=json.load(response)
                self.assertEqual(info['count'],8)
                self.assertIsInstance(info['source_signature']['mtime_ns'],str,'JS must preserve nanosecond file identity')
                payload=self.payload()
                with urlopen(Request(base+'/api/model-crop/preview',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})) as response:
                    blob=response.read()
                length=int.from_bytes(blob[:4],'little');head=json.loads(blob[4:4+length]);mask=blob[4+length:]
                self.assertEqual(len(mask),8);self.assertEqual(head['kept'],4)
                self.assertEqual(head['selection_sha256'],hashlib.sha256(mask).hexdigest())
                for value in [{**payload,'model':'../outside.ply'}, {**payload,'crop':{**self.box,'min':[9,9,9]}}]:
                    with self.assertRaises(HTTPError) as exc: urlopen(Request(base+'/api/model-crop/preview',data=json.dumps(value).encode()))
                    self.assertEqual(exc.exception.code,422)
            finally:
                server.shutdown();server.server_close()
