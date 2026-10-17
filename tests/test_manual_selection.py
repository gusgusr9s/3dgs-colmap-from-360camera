"""Projection, front-layer protection, immutable selections and exact saved vertices."""
import hashlib
import json
import struct
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

import numpy as np
from plyfile import PlyData
import manual_selection as manual
import model_crop as crop
import web_app as app
import test_model_crop as fixtures


class ManualSelectionTests(unittest.TestCase):
    write_source = fixtures.CropTests.write_source
    payload = fixtures.CropTests.payload
    wait = fixtures.CropTests.wait

    def setUp(self):
        fixtures.CropTests.setUp(self)
        selection_patch = patch.object(manual,'SELECTIONS',self.root/'selections')
        selection_patch.start();self.addCleanup(selection_patch.stop)
        self.vertices['x'] = [0,0,.8,-.8,0,10,0,0]
        self.vertices['y'] = 0
        self.vertices['z'] = [-2,-4,-2,-2,3,-2,-2,-.05]
        self.vertices['opacity'][6] = -20
        self.write_source()
        self.box.update(min=[-20]*3,max=[20]*3,strict=False)
        self.screen = {'tool':'brush','points':[[100,100]],'viewport':[200,200], 'radius':10,
                       'view':np.eye(4).tolist(),'view_projection':[[1,0,0,0],[0,1,0,0],
                       [0,0,-20.1/19.9,-4/19.9],[0,0,-1,0]],'front_only':True}

    def test_brush_front_layer_through_and_lasso(self):
        eligible = np.ones(8,dtype=np.uint8)
        self.assertEqual(np.flatnonzero(manual.screen_hits(self.vertices,eligible,self.screen)).tolist(),[0])
        through = {**self.screen,'front_only':False}
        self.assertEqual(np.flatnonzero(manual.screen_hits(self.vertices,eligible,through)).tolist(),[0,1])
        eligible[0]=0
        self.assertEqual(np.flatnonzero(manual.screen_hits(self.vertices,eligible,self.screen)).tolist(),[1])
        lasso = {**self.screen,'tool':'lasso','points':[[130,90],[150,90],[150,110],[130,110]]}
        self.assertEqual(np.flatnonzero(manual.screen_hits(self.vertices,eligible,lasso)).tolist(),[2])
        # Brush segment must include its middle, not just the sampled endpoints.
        line = {**through,'radius':2,'points':[[50,100],[150,100]]}
        self.assertEqual(np.flatnonzero(manual.screen_hits(self.vertices,np.ones(8),line)).tolist(),[0,1,2,3])

    def test_selection_history_highlight_exact_save_and_parent(self):
        original = crop.sha256_file(self.source); request=self.payload()
        selected=crop.propose_selection(self.models,{**request,'screen':self.screen})
        self.assertEqual(selected['added'],1)
        first=selected['selection_id']
        repeated=crop.propose_selection(self.models,{**request,'screen':self.screen,'base_selection_id':first})
        self.assertEqual(repeated['added'],0,'pending red points protect deeper layers until Apply')
        pending={**request,'highlight_selection_id':first}
        keep,_,highlight=crop.preview(self.models,pending,with_highlight=True)
        self.assertEqual(int(keep.sum()),8); self.assertEqual(np.flatnonzero(highlight).tolist(),[0])
        with self.assertRaises(ValueError):crop.start_edit(self.models,pending,{},app.task_lock_alive)
        committed={**request,'manual_selection_id':first}
        mask,digest=crop.preview(self.models,committed)
        self.assertEqual(np.flatnonzero(mask==0).tolist(),[0])
        # A second stroke selects the newly exposed layer. Undo returns immutable first mask.
        second=crop.propose_selection(self.models,{**committed,'screen':self.screen,'base_selection_id':first})
        self.assertEqual(second['added'],1)
        keep2,_=crop.preview(self.models,{**request,'manual_selection_id':second['selection_id']})
        self.assertEqual(np.flatnonzero(keep2==0).tolist(),[0,1])
        np.testing.assert_array_equal(crop.preview(self.models,committed)[0],mask)
        result=crop.start_edit(self.models,{**committed,'selection_sha256':digest}, {'quality':{'psnr':30}},app.task_lock_alive)
        state=self.wait(result['id']);self.assertEqual(state['state'],'ready',state)
        actual=PlyData.read(self.models/state['model'])['vertex'].data
        for name in self.vertices.dtype.names:
            np.testing.assert_array_equal(actual[name],self.vertices[mask.astype(bool)][name])
        self.assertEqual(crop.sha256_file(self.source),original)
        metadata=json.loads((self.models/state['model']).with_suffix('.meta.json').read_text(encoding='utf8'))
        self.assertEqual(metadata['edit']['manual_selection_id'],first)
        self.assertEqual(metadata['quality'],{'published_splats':7})
        self.assertEqual(metadata['parent_quality']['psnr'],30)

    def test_validation_stale_identity_corrupt_mask_and_empty_stroke(self):
        for bad in [{**self.screen,'viewport':[1,200]}, {**self.screen,'view':np.ones((4,4)).tolist()},
                    {**self.screen,'radius':float('nan')},{**self.screen,'front_only':'true'},
                    {**self.screen,'tool':'lasso','points':[[1,1],[2,2],[3,3]]},
                    {**self.screen,'points':[[float('inf'),1]]}, {**self.screen,'points':[[201,1]]}]:
            with self.assertRaises(ValueError):manual.validate_screen(bad)
        request=self.payload()
        empty=crop.propose_selection(self.models,{**request,'screen':{**self.screen,'points':[[10,10]]}})
        self.assertEqual(empty,{'selection_id':None,'added':0,'removed_total':0})
        selected=crop.propose_selection(self.models,{**request,'screen':self.screen})['selection_id']
        for identifier in ['../outside','g'*32,'a'*31]:
            with self.assertRaises(ValueError):manual.selection_directory(identifier)
        with self.assertRaises(ValueError):crop.preview(self.models,{**request,'manual_selection_id':selected,'source_sha256':'0'*64})
        directory=manual.selection_directory(selected)
        (directory/'removed.bin').write_bytes(b'\xff')
        with self.assertRaises(ValueError):crop.preview(self.models,{**request,'manual_selection_id':selected})

    def test_http_select_and_highlight_binary_contract(self):
        with patch.object(app,'MODELS',self.models):
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{server.server_port}'
            def post(path,payload):
                return urlopen(Request(base+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}))
            try:
                request=self.payload()
                with post('/api/model-crop/select',{**request,'screen':self.screen}) as response:selected=json.load(response)
                with post('/api/model-crop/preview',{**request,'highlight_selection_id':selected['selection_id']}) as response:blob=response.read()
                length=struct.unpack_from('<I',blob)[0]; head=json.loads(blob[4:4+length]); masks=blob[4+length:]
                self.assertEqual(head['highlight_bytes'],8);self.assertEqual(head['highlight_count'],1)
                self.assertEqual(head['selection_sha256'],hashlib.sha256(masks[:8]).hexdigest())
                self.assertEqual(list(masks[8:]),[1,0,0,0,0,0,0,0])
            finally:server.shutdown();server.server_close()
