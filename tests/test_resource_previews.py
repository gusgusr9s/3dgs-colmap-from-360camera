"""Real image decoding and HTTP preview routes with isolated artifacts."""
from html.parser import HTMLParser
import gzip
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import server as app
from plyfile import PlyData, PlyElement


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='preview_check_', dir=app.ROOT/'web_data')
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_relative_to((app.ROOT/'web_data').resolve())
        directories = {key: self.root/key.lower() for key in ('RAW', 'UPLOADS', 'JOBS', 'MODELS', 'PREVIEWS', 'EXPERIMENTS')}
        for directory in directories.values():
            directory.mkdir()
        self.patches = [patch.object(app, key, value) for key, value in directories.items()]
        self.patches += [patch.object(app, 'SOURCE_DIRECTORIES', {'raw': directories['RAW'], 'uploads': directories['UPLOADS']}), patch.object(app.Handler, 'log_message')]
        for item in self.patches:
            item.start()
        self.source = app.RAW/'scene & space.jpg'
        self.write_image(self.source, (0, 0, 220))
        self.job = app.JOBS/'jobA'
        self.job.mkdir()
        self.key = 'data/raw:' + self.source.name
        app.save_job_manifest('jobA', {'label': 'Scene', 'source_key': self.key, 'source_name': self.source.name, 'settings': app.DEFAULT_VIEW_SETTINGS})
        frames = []
        for index, color in enumerate(((0, 0, 220), (0, 220, 0), (220, 0, 0), (0, 220, 220))):
            name = f'frame_{index:06d}_view_00.jpg'
            self.write_image(self.job/name, color)
            frames.append({'file_path': name, 'frame_index': index, 'pitch_deg': 0})
        app.write_json(self.job/'transforms.json', {'frames': frames})
        app.write_json(self.job/'views_status.json', {'state': 'ready', 'run_id': 'views-new'})
        self.runs = ['20261008_010000_1234abcd', '20261008_020000_abcd1234']
        for identifier, name, color in zip(self.runs, ['old.ply', 'new.ply'], [(0, 220, 0), (220, 0, 0)]):
            self.write_ply(app.MODELS/name)
            run = self.job/'model_runs'/identifier
            self.write_image(run/'brush_export'/'eval_100'/'frame_000000_view_00.png', color)
            self.write_image(run/'dataset'/'images'/'original.jpg', color)
            app.write_json(run/'dataset'/'transforms.json', {'frames': [{'file_path':'images/original.jpg', 'frame_index':0}]})
            status = {'state':'done', 'model':name, 'job':'jobA', 'run_id':identifier, 'source_key':self.key, 'view_run_id':'views-old', 'quality':{'training_steps':100}}
            app.write_json(run/'status.json', status)
            app.write_json(app.model_metadata_path(name), status)
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write_image(self, path, color):
        path.parent.mkdir(parents=True, exist_ok=True)
        image = app.np.full((48, 64, 3), color, app.np.uint8)
        self.assertTrue(app.cv2.imwrite(str(path), image))

    def write_ply(self, path):
        random = app.np.random.default_rng(5)
        vertices = app.np.zeros(500, dtype=[('x','f4'), ('y','f4'), ('z','f4'), ('red','u1'), ('green','u1'), ('blue','u1')])
        for name in ('x','y','z'):
            vertices[name] = random.normal(size=len(vertices))
        vertices['red'], vertices['green'], vertices['blue'] = 220, 120, 80
        vertices['x'][0] = app.np.nan
        PlyData([PlyElement.describe(vertices, 'vertex')]).write(str(path))

    def preview(self, kind, key):
        with urlopen(self.base + app.resource_preview_url(kind, key)) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
            self.assertEqual(response.headers['Cache-Control'], 'private, no-cache')
            data = response.read()
        self.assertLess(len(data), 100000)
        image = app.cv2.imdecode(app.np.frombuffer(data, app.np.uint8), app.cv2.IMREAD_COLOR)
        self.assertEqual(image.shape, (216, 384, 3))
        return data, image

    def test_source_cache_reuse_and_invalidation(self):
        first, image = self.preview('source', self.key)
        self.assertGreater(int(image[108,192,2]), 200)
        cache = list(app.PREVIEWS.glob('*.jpg'))
        modified = cache[0].stat().st_mtime_ns
        self.assertEqual(first, self.preview('source', self.key)[0])
        self.assertEqual(modified, cache[0].stat().st_mtime_ns)
        self.write_image(self.source, (0, 220, 0))
        stat = self.source.stat()
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        second, image = self.preview('source', self.key)
        self.assertNotEqual(first, second)
        self.assertGreater(int(image[108,192,1]), 200)

    def test_video_proxy_status_validation_and_range_requests(self):
        from app.web.video_preview import preview_directory
        source = app.UPLOADS / 'browser clip.mp4'
        source.write_bytes(b'original video remains unchanged')
        query = urlencode({'source':'uploads:'+source.name})
        url = self.base + '/api/video-preview?' + query
        with urlopen(url) as response:
            self.assertEqual(json.load(response)['state'], 'idle')
        directory = preview_directory(source, app.PREVIEWS/'video')
        directory.mkdir(parents=True)
        (directory/'preview.mp4').write_bytes(b'cached browser video')
        app.write_json(directory/'status.json', {'state':'ready','progress':100})
        with urlopen(Request(url, method='POST')) as response:
            record = json.load(response)
        self.assertEqual(record['state'], 'ready')
        with urlopen(Request(self.base+record['url'], headers={'Range':'bytes=7-13'})) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers['Content-Type'], 'video/mp4')
            self.assertEqual(response.headers['Content-Range'], 'bytes 7-13/20')
            self.assertEqual(response.read(), b'browser')
        self.assertEqual(source.read_bytes(), b'original video remains unchanged')
        for route in ['/api/video-preview?source=uploads:../outside.mp4',
                      '/api/video-preview?'+urlencode({'source':self.key}),
                      record['url'].replace(record['id'], 'stale')]:
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base+route)
            self.assertIn(error.exception.code, (404, 409))
        source.write_bytes(b'replaced original')
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base+record['url'])
        self.assertEqual(error.exception.code, 409)

    def test_switching_video_can_abort_headers_or_body_without_server_errors(self):
        handler = object.__new__(app.Handler)
        handler.headers = {}
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock(side_effect=ConnectionAbortedError('video switched'))
        handler.wfile = Mock()
        handler.send_data(b'preview', 'video/mp4', 200, 'no-store')
        handler.send_file(self.source)
        handler.wfile.write.assert_not_called()
        handler.end_headers = Mock()
        handler.wfile.write.side_effect = ConnectionResetError('video switched')
        handler.send_data(b'preview', 'video/mp4', 200, 'no-store')
        handler.send_file(self.source)

    def test_preview_http_revalidation_tracks_changed_input(self):
        url = self.base + app.resource_preview_url('source', self.key)
        with urlopen(url) as response:
            etag = response.headers['ETag']
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(url, headers={'If-None-Match': etag}))
        self.assertEqual(error.exception.code, 304)
        self.write_image(self.source, (0, 220, 0))
        stat = self.source.stat()
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        with urlopen(Request(url, headers={'If-None-Match': etag})) as response:
            self.assertEqual(response.status, 200)
            self.assertNotEqual(response.headers['ETag'], etag)

    def test_html_compression_preserves_page_and_honors_opt_out(self):
        with urlopen(self.base+'/models') as response:
            plain = response.read()
        with urlopen(Request(self.base+'/models',headers={'Accept-Encoding':'gzip, deflate'})) as response:
            compressed = response.read()
            self.assertEqual(response.headers['Content-Encoding'],'gzip')
            self.assertEqual(gzip.decompress(compressed),plain)
            self.assertLess(len(compressed),len(plain)/2)
            self.assertEqual(response.headers['Vary'],'Accept-Encoding')
        with urlopen(Request(self.base+'/models',headers={'Accept-Encoding':'gzip;q=0'})) as response:
            self.assertIsNone(response.headers.get('Content-Encoding'))
            self.assertEqual(response.read(),plain)

    def test_views_mosaic_and_individual_files(self):
        _, mosaic = self.preview('views', 'jobA')
        for y, x, channel in [(54,96,2), (54,288,1), (162,96,0), (162,288,1)]:
            self.assertGreater(int(mosaic[y,x,channel]), 200)
        _, image = self.preview('view-image', 'jobA/frame_000002_view_00.jpg')
        self.assertGreater(int(image[108,192,0]), 200)

    def test_model_renders_keep_their_snapshot_after_reprocessing(self):
        old, image = self.preview('model', 'old.ply')
        self.assertGreater(int(image[108,192,1]), 200)
        _, image = self.preview('model', 'new.ply')
        self.assertGreater(int(image[108,192,0]), 200)
        for image in self.job.glob('frame_*.jpg'):
            self.write_image(image, (0, 0, 220))
        self.assertEqual(old, self.preview('model', 'old.ply')[0])
        _, image = self.preview('view-image', 'jobA/model_runs/'+self.runs[0]+'/dataset/images/original.jpg')
        self.assertGreater(int(image[108,192,1]), 200)

    def test_uploaded_ply_has_a_geometry_preview(self):
        self.write_ply(app.MODELS/'uploaded.ply')
        paths, mode, label = app.resource_preview_assets('model', 'uploaded.ply')
        self.assertEqual(mode, 'ply')
        self.assertIn('จุด 3D', label)
        self.assertEqual(paths, [app.MODELS/'uploaded.ply'])
        _, image = self.preview('model', 'uploaded.ply')
        self.assertGreater(int(image[:,:,2].max()), 200)

    def test_model_preview_prefers_a_horizontal_render(self):
        run = self.job/'model_runs'/self.runs[0]
        self.write_image(run/'brush_export'/'eval_100'/'ceiling.png', (0, 0, 220))
        app.write_json(run/'dataset'/'transforms.json', {'frames':[
            {'file_path':'images/frame_000000_view_00.jpg', 'pitch_deg':0, 'yaw_deg':0},
            {'file_path':'images/ceiling.jpg', 'pitch_deg':55, 'yaw_deg':0}]})
        paths, _, _ = app.resource_preview_assets('model', 'old.ply')
        self.assertEqual(paths[0].name, 'frame_000000_view_00.png')
        _, image = self.preview('model', 'old.ply')
        self.assertGreater(int(image[108,192,1]), 200)

    def test_video_preview_and_poster(self):
        # Users can remove bundled samples from Sources; tests own their fixtures.
        writer=app.cv2.VideoWriter(str(app.UPLOADS/'clip.mp4'), app.cv2.VideoWriter_fourcc(*'mp4v'), 10, (320,160))
        self.assertTrue(writer.isOpened())
        pixels=app.np.zeros((160,320,3),app.np.uint8)
        pixels[:,:160]=(20,80,220);pixels[:,160:]=(220,180,20)
        for _ in range(10):writer.write(pixels)
        writer.release()
        _, image = self.preview('source', 'uploads:clip.mp4')
        self.assertGreater(float(image.std()), 15)
        with urlopen(self.base+'/preview/uploads/clip.mp4') as response:
            self.assertIn('poster="/api/resource-preview?', response.read().decode())

    def test_corrupt_or_unavailable_inputs_get_a_placeholder(self):
        (app.UPLOADS/'broken.jpg').write_bytes(b'not an image')
        self.preview('source', 'uploads:broken.jpg')
        (app.MODELS/'broken.ply').write_bytes(b'ply\n')
        self.preview('model', 'broken.ply')
        self.job.joinpath('transforms.json').write_text('{"frames":[]}')
        for path in self.job.glob('frame_*.jpg'):
            path.unlink()
        self.preview('views', 'jobA')
        (app.RAW/'raw.insv').write_bytes(b'raw camera file')
        self.preview('source', 'data/raw:raw.insv')
        self.assertIn('ต้อง Export', app.resource_preview_markup('source', 'data/raw:raw.insv', '/preview/raw/raw.insv'))

    def test_invalid_paths_are_not_served(self):
        for kind, key in [('model','../raw/scene & space.jpg'), ('model','missing.ply'), ('views','../models'), ('view-image','../raw/scene & space.jpg'), ('view-image','jobA/transforms.json'), ('source','data/raw:../scene.jpg'), ('unknown','jobA')]:
            with self.assertRaises(HTTPError) as error:
                self.preview(kind, key)
            self.assertEqual(error.exception.code, 404)
        app.write_json(self.job/'transforms.json', {'frames':[{'file_path': '../raw/scene & space.jpg'}]})
        self.assertTrue(all(path.is_relative_to(self.job) for path in app.preview_images(self.job)))

    def test_cards_and_galleries_link_to_real_files(self):
        class Images(HTMLParser):
            def __init__(self, page):
                super().__init__(); self.kinds = []; self.hrefs = []; self.feed(page)
            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == 'img' and attributes.get('src','').startswith('/api/resource-preview?'):
                    self.kinds.append(parse_qs(urlparse(attributes['src']).query)['kind'][0])
                if tag == 'a':
                    self.hrefs.append(attributes.get('href',''))
        for route, expected in [('/', {'views'}), ('/sources', {'source'}), ('/jobs', {'views'}), ('/models', {'model'}), ('/preview/raw/scene%20%26%20space.jpg', {'views','model'}), ('/job/jobA', {'model','view-image'}), ('/model-inputs/old.ply', {'view-image'})]:
            with urlopen(self.base + route) as response:
                parser = Images(response.read().decode())
            self.assertTrue(expected.issubset(set(parser.kinds)), (route, parser.kinds))
            self.assertFalse(any(href.startswith('/api/resource-preview') for href in parser.hrefs))


if __name__ == '__main__':
    unittest.main()
