"""Scene creation, save/reopen, asset resolution and collision mesh over HTTP."""
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.models import scene_projects
from app.web import server as app


class SceneHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='scene_http_')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.models, self.jobs = root/'models', root/'jobs'
        self.models.mkdir(); self.jobs.mkdir()
        for name in ['room_a.ply', 'room_b.ply']:
            (self.models/name).write_bytes(b'ply\nformat ascii 1.0\nelement vertex 0\nend_header\n')
            (self.models/Path(name).with_suffix('.meta.json')).write_text(
                json.dumps({'viewer_config': {}, 'world_up': [0,1,0]}), encoding='utf8')
        identifier = 'a'*32
        self.compressed = root/'spz'; self.compressed.mkdir()
        (self.compressed/'model.spz').write_bytes(b'fixture')
        self.record = {'id':identifier, 'state':'ready', 'model':'room_b.ply', 'parent_info':{}}
        for context in [patch.multiple(app, MODELS=self.models, JOBS=self.jobs),
                        patch.object(scene_projects, 'SCENES', root/'scenes'),
                        patch.object(app, 'compression_records', return_value=[self.record]),
                        patch.object(app, 'compression_record', side_effect=lambda key: self.record if key==identifier else None),
                        patch.object(app, 'compression_directory', return_value=self.compressed),
                        patch.object(app.Handler, 'log_message')]:
            context.start(); self.addCleanup(context.stop)
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def get(self, path):
        with urlopen(self.base+path) as response:
            return response.status, response.read()

    def post(self, path, data):
        request = Request(self.base+path, data=json.dumps(data).encode(), headers={'Content-Type':'application/json'})
        with urlopen(request) as response:
            return response.status, json.load(response)

    def test_multi_model_save_reopen_and_actual_collision_triangles(self):
        status, scene = self.post('/api/scenes', {'name':'สองสถานที่', 'objects':[
            {'asset':{'kind':'ply','ref':'room_a.ply'},'position':[0,0,0]},
            {'asset':{'kind':'spz','ref':'a'*32},'position':[10,0,0],'rotation':[0,math.pi/2,0],'scale':2}],
            'colliders':[{'kind':'floor','position':[0,-.1,0],'size':[20,.2,10]},
                         {'kind':'wall','position':[5,1.5,0],'size':[8,3,.2],'yaw':math.pi/2}]})
        self.assertEqual(status,201)
        original = (self.models/'room_a.ply').read_bytes()
        identifier = scene['id']
        scene['objects'][1]['position'][0] = 12
        status, saved = self.post('/api/scenes/'+identifier,scene)
        self.assertEqual(status,200)
        self.assertGreater(saved['revision'],scene['revision'])
        reopened = json.loads(self.get('/api/scenes/'+identifier)[1])
        self.assertEqual(reopened,saved)
        self.assertEqual(reopened['objects'][1]['position'],[12,0,0])
        self.assertEqual((self.models/'room_a.ply').read_bytes(),original)
        self.assertEqual(json.loads(self.get('/scene-download/'+identifier+'.json')[1]),saved)
        obj = self.get('/scene-collision/'+identifier+'.obj')[1].decode()
        self.assertEqual(sum(line.startswith('v ') for line in obj.splitlines()),16)
        self.assertEqual(sum(line.startswith('f ') for line in obj.splitlines()),24)
        vertices = [list(map(float,line.split()[1:])) for line in obj.splitlines() if line.startswith('v ')][8:]
        self.assertAlmostEqual(max(v[0] for v in vertices)-min(v[0] for v in vertices),.2)
        self.assertAlmostEqual(max(v[2] for v in vertices)-min(v[2] for v in vertices),8)
        with self.assertRaises(HTTPError) as failure:
            self.post('/api/scenes/'+identifier,scene)
        self.assertEqual(failure.exception.code,409)
        self.assertEqual(json.loads(self.get('/api/scenes/'+identifier)[1]),saved)

    def test_catalog_pages_static_paths_invalid_payloads_and_delete(self):
        catalog = json.loads(self.get('/api/scene-assets')[1])['assets']
        self.assertEqual({asset['kind'] for asset in catalog},{'ply','spz'})
        self.assertEqual(len(catalog),3)
        _, scene = self.post('/api/scenes',{'name':'<script>ฉาก</script>'})
        page = self.get('/scene/'+scene['id'])[1].decode()
        self.assertNotIn('__CONFIG__',page)
        self.assertNotIn('__TITLE__',page)
        self.assertIn('/assets/scene_editor.mjs',page)
        listing = self.get('/scenes')[1].decode()
        self.assertIn('&lt;script&gt;ฉาก&lt;/script&gt;',listing)
        self.assertNotIn('<script>ฉาก</script>',listing)
        self.assertIn('href="/scenes"',self.get('/models')[1].decode())
        for name in ['scene_editor.mjs','scene_editor.css','scene_collision.mjs','scene_library.mjs']:
            self.assertEqual(self.get('/assets/'+name)[0],200)
        for payload in [{'objects':[{'asset':{'kind':'ply','ref':'../room_a.ply'}}]},
                        {'objects':[{'asset':{'kind':'spz','ref':'missing'}}]},
                        {'colliders':[{'size':[1,-1,1]}]}]:
            with self.assertRaises(HTTPError) as failure:
                self.post('/api/scenes',payload)
            self.assertEqual(failure.exception.code,422)
        for path in ['/api/scenes/../outside','/scene/../outside','/scene-download/../outside.json']:
            with self.assertRaises(HTTPError) as failure: self.get(path)
            self.assertEqual(failure.exception.code,404)
        self.post('/api/scenes/'+scene['id']+'/delete',{'revision':scene['revision']})
        with self.assertRaises(HTTPError) as failure: self.get('/api/scenes/'+scene['id'])
        self.assertEqual(failure.exception.code,404)


if __name__ == '__main__':
    unittest.main()
