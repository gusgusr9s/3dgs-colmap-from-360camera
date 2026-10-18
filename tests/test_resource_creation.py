"""Resource collisions, concurrent requests and interrupted uploads stay isolated."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection, HTTPResponse
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import server as app
from app.jobs import resource_names as names


def multipart(action, filename, content, field='file', closed=True):
    boundary = 'resource-test-boundary'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\n{action}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="return_to"\r\n\r\n/sources\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + content
    if closed:
        body += f'\r\n--{boundary}--\r\n'.encode()
    return body, 'multipart/form-data; boundary=' + boundary


class ResourceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app.ROOT/'web_data', prefix='resource_creation_')
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_relative_to(app.ROOT/'web_data')
        self.jobs, self.models, self.uploads = [self.root/name for name in ('jobs','models','uploads')]
        for directory in (self.jobs,self.models,self.uploads):
            directory.mkdir()

    def tearDown(self):
        self.temp.cleanup()


class ResourceTests(ResourceFixture):
    def test_labels_ignore_case_whitespace_and_keep_length_limit(self):
        self.assertEqual(names.unique_label(' Room  test ', ['room test','Room test (2)']), 'Room test (3)')
        label = names.unique_label('ก'*100, ['ก'*100])
        self.assertEqual(len(label),100)
        self.assertTrue(label.endswith(' (2)'))

    def test_id_collision_retries_and_preserves_old_job_and_archived_model_id(self):
        old = self.jobs/'aaaaaaaaaa'
        old.mkdir()
        sentinel = old/'job.json'
        sentinel.write_bytes(b'original metadata')
        identifiers = [SimpleNamespace(hex=value*32) for value in ('a','b','c')]
        with patch.object(names.uuid,'uuid4',side_effect=identifiers):
            directory = names.reserve_job_directory(self.jobs, {'bbbbbbbbbb'})
        self.assertEqual(directory.name,'cccccccccc')
        self.assertEqual(sentinel.read_bytes(),b'original metadata')

    def test_concurrent_jobs_get_different_labels_without_modifying_old_files(self):
        old = self.jobs/'old'
        old.mkdir()
        app.write_json(old/'job.json',dict(label='DJI room', untouched=True))
        original = (old/'job.json').read_bytes()
        with patch.multiple(app,JOBS=self.jobs,MODELS=self.models):
            with ThreadPoolExecutor(max_workers=4) as pool:
                created = list(pool.map(lambda _: app.create_job_record(Path('DJI.mp4'),'uploads:DJI.mp4',{},'DJI room'),range(4)))
        self.assertEqual(len({identifier for identifier,_ in created}),4)
        self.assertEqual({label for _,label in created},{f'DJI room ({number})' for number in range(2,6)})
        self.assertEqual((old/'job.json').read_bytes(),original)

    def test_new_job_does_not_reuse_job_id_retained_by_a_renamed_model(self):
        app.write_json(self.models/'renamed.meta.json',dict(job='aaaaaaaaaa',model='renamed.ply'))
        (self.models/'renamed.ply').write_bytes(b'original model')
        identifiers = [SimpleNamespace(hex=value*32) for value in ('a','b','c')]
        with patch.multiple(app,JOBS=self.jobs,MODELS=self.models), patch.object(app,'compression_records',return_value=[]), patch.object(names.uuid,'uuid4',side_effect=identifiers):
            identifier, label = app.create_job_record(Path('DJI.mp4'),'uploads:DJI.mp4',{},'DJI room')
        self.assertEqual(identifier,'bbbbbbbbbb')
        self.assertEqual((self.models/'renamed.ply').read_bytes(),b'original model')

    def test_concurrent_uploads_do_not_replace_each_other_or_original(self):
        original = self.uploads/'DJI.mp4'
        original.write_bytes(b'old video')
        payloads = [b'new video '+bytes([number]) for number in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(lambda data:names.publish_upload(self.uploads,'DJI.mp4',io.BytesIO(data)),payloads))
        self.assertEqual(len(set(paths)),4)
        self.assertEqual({path.read_bytes() for path in paths},set(payloads))
        self.assertEqual(original.read_bytes(),b'old video')
        self.assertFalse(list(self.uploads.glob('*.partial')))

    def test_partial_copy_and_validation_failure_never_publish_a_source(self):
        class BrokenStream(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError('connection interrupted')
                return super().read(3)
        for stream in (BrokenStream(b'video data'), io.BytesIO(b'')):
            with self.assertRaises((OSError,ValueError)):
                names.publish_upload(self.uploads,'DJI.mp4',stream)
            self.assertEqual(list(self.uploads.iterdir()),[])
        def validate(path):
            self.assertEqual(list(self.uploads.glob('*.mp4')),[])
            self.assertEqual(path.read_bytes(),b'complete video')
        target = names.publish_upload(self.uploads,'DJI.mp4',io.BytesIO(b'complete video'),validate)
        self.assertEqual(target.read_bytes(),b'complete video')


class ActionHttpTests(ResourceFixture):
    def setUp(self):
        super().setUp()
        self.patches = [patch.multiple(app,JOBS=self.jobs,MODELS=self.models,UPLOADS=self.uploads,
            RAW=self.root,ACTIVE_TASKS=set(),SOURCE_DIRECTORIES={'raw':self.root,'uploads':self.uploads}),
            patch.object(app.Handler,'log_message')]
        for item in self.patches:
            item.start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def post(self, body, content_type, ajax=True):
        connection = HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        headers = {'Content-Type':content_type}
        if ajax:
            headers['X-Requested-With'] = 'XMLHttpRequest'
        connection.request('POST','/actions',body,headers)
        response = connection.getresponse()
        result = (response.status,dict(response.getheaders()),response.read())
        connection.close()
        return result

    def test_uploads_keep_original_name_and_number_duplicates_with_json_feedback(self):
        for number in (1,2):
            body, kind = multipart('upload_source','DJI.mp4',f'video {number}'.encode())
            status, _, data = self.post(body,kind)
            result = json.loads(data)
            self.assertEqual(status,200)
            self.assertTrue(result['ok'])
            self.assertIn('อัปโหลด',result['message'])
            self.assertTrue(result['redirect_to'].startswith('/sources?'))
        self.assertEqual((self.uploads/'DJI.mp4').read_bytes(),b'video 1')
        self.assertEqual((self.uploads/'DJI (2).mp4').read_bytes(),b'video 2')

    def test_invalid_model_upload_returns_error_and_preserves_previous_model(self):
        original = self.models/'room.ply'
        original.write_bytes(b'ply\noriginal')
        body, kind = multipart('upload_model','room.ply',b'not a PLY','model')
        status, _, data = self.post(body,kind)
        self.assertEqual(status,400)
        self.assertFalse(json.loads(data)['ok'])
        self.assertEqual(original.read_bytes(),b'ply\noriginal')
        self.assertEqual([path.name for path in self.models.iterdir()],['room.ply'])

    def test_interrupted_multipart_does_not_add_incomplete_video(self):
        body, kind = multipart('upload_source','DJI.mp4',b'incomplete video',closed=False)
        with socket.create_connection(('127.0.0.1',self.server.server_port),timeout=10) as connection:
            headers = f'POST /actions HTTP/1.1\r\nHost: localhost\r\nContent-Type: {kind}\r\nContent-Length: {len(body)+100}\r\nX-Requested-With: XMLHttpRequest\r\n\r\n'
            connection.sendall(headers.encode()+body)
            connection.shutdown(socket.SHUT_WR)
            response = HTTPResponse(connection)
            response.begin()
            result = json.loads(response.read())
            self.assertEqual(response.status,400)
            self.assertFalse(result['ok'])
        self.assertEqual(list(self.uploads.iterdir()),[])

    def test_native_form_redirect_still_works(self):
        body, kind = multipart('upload_source','native.mp4',b'video')
        status, headers, _ = self.post(body,kind,ajax=False)
        self.assertEqual(status,303)
        self.assertTrue(headers['Location'].startswith('/sources?'))

    def test_renaming_job_to_existing_label_assigns_unique_name(self):
        for identifier,label in (('first','Room'),('second','Other room')):
            directory = self.jobs/identifier
            directory.mkdir()
            app.write_json(directory/'job.json',dict(label=label,source_name='room.png',source_key='uploads:room.png',settings=app.DEFAULT_VIEW_SETTINGS))
        original = (self.jobs/'first/job.json').read_bytes()
        body = urlencode(dict(action='update_job',job='second',label='room',return_to='/jobs')).encode()
        status, _, data = self.post(body,'application/x-www-form-urlencoded')
        self.assertEqual(status,200,json.loads(data))
        self.assertEqual(app.read_json(self.jobs/'second/job.json')['label'],'room (2)')
        self.assertEqual((self.jobs/'first/job.json').read_bytes(),original)

    def test_generate_duplicate_label_creates_new_job_and_start_failure_is_visible(self):
        source = self.uploads/'room.png'
        app.cv2.imwrite(str(source),app.np.zeros((32,64,3),app.np.uint8))
        body = urlencode(dict(action='generate',source='uploads:room.png',label='room',quality='preview')).encode()
        with patch.object(app,'start_views',return_value=True) as start:
            for _ in range(2):
                status, _, data = self.post(body,'application/x-www-form-urlencoded')
                self.assertEqual(status,200,json.loads(data))
            self.assertEqual(start.call_count,2)
        labels = {app.read_json(path/'job.json')['label'] for path in self.jobs.iterdir()}
        self.assertEqual(labels,{'room','room (2)'})
        with patch.object(app,'start_views',return_value=False):
            status, _, data = self.post(body,'application/x-www-form-urlencoded')
            self.assertEqual(status,400)
            self.assertFalse(json.loads(data)['ok'])


if __name__ == '__main__':
    unittest.main()
