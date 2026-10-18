"""HTTP contracts for the optional preparation page and exact model camera links."""
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from app.web import server as app
import test_task_history as history_fixture
from app.jobs.processing_queue import processing_slot


class PreparationFlowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = history_fixture.HistoryTests('test_timer_and_bangkok_display')
        self.fixture.setUp()
        self.job = self.fixture.job
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.fixture.tearDown()

    def get(self,path,data=None):
        with urlopen(self.base+path,data=urlencode(data).encode() if data else None) as response:
            return response.read()

    def test_review_is_optional_and_submit_preserves_options(self):
        page=self.get('/prepare/'+self.job).decode()
        self.assertIn('สร้าง Model ด้วยค่าที่เลือก',page)
        self.assertIn('name="filter_blur" value="1" checked',page)
        self.assertIn('/assets/preparation.mjs',page)
        with patch.object(app,'start_3d',return_value=True) as start:
            self.get('/actions',dict(action='generate3d',job=self.job,preparation_options='1',
                floor='more_floor',detail='source_2048',filter_blur='1',return_to='/job/'+self.job))
        self.assertEqual(start.call_args.kwargs['options'],
                         dict(floor='more_floor',detail='source_2048',filter_blur=True))

    def test_busy_job_disables_form_and_preview_rejects_unknown_frame(self):
        with patch.object(app,'task_is_active',return_value=True):
            page=self.get('/prepare/'+self.job).decode()
        self.assertIn('<fieldset disabled>',page)
        with self.assertRaises(HTTPError) as error:
            self.get('/api/preparation-image/'+self.job+'?frame=../../secret')
        self.assertEqual(error.exception.code,422)
        data=self.get('/api/preparation-image/'+self.job+'?frame=frame_000000_view_00&floor=standard')
        self.assertTrue(data.startswith(b'\xff\xd8'))

    def test_too_few_positions_fail_before_claiming_or_allocating(self):
        cameras = [{'frame_index': i, 'views': [{}] * 8} for i in range(3)]
        with patch.object(app, 'cached_pose_preview', return_value={'cameras': cameras}), \
             patch.object(app, 'claim_task') as claim, patch.object(app, 'allocate_model_run') as allocate:
            with self.assertRaisesRegex(ValueError, '4'):
                app.start_3d(self.job)
            claim.assert_not_called()
            allocate.assert_not_called()

    def test_user_can_cancel_waiting_job_without_starting_trainer(self):
        with patch.object(app.subprocess, 'run', side_effect=AssertionError('Queued task started')):
            with processing_slot(self.fixture.root/'processing_queue', app.task_lock_alive):
                self.assertTrue(app.start_3d(self.job))
                deadline=time.monotonic()+5
                while (app.model_status(self.job) or {}).get('state')!='queued' and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertEqual(app.model_status(self.job)['state'],'queued')
                app.write_json(self.fixture.directory/'pose_status.json', {'state':'error', 'error':'Previous attempt'})
                self.assertEqual(app.combined_job_status(self.job)['state'], 'queued')
                self.get('/actions',dict(action='cancel_queued',job=self.job,return_to='/job/'+self.job))
                deadline=time.monotonic()+5
                while app.task_is_active(self.job) and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertFalse(app.task_is_active(self.job))
                self.assertEqual(app.model_status(self.job)['state'],'cancelled')
                self.assertEqual(list(self.fixture.models.glob('*.ply')),[])

    def test_quality_link_resolves_exact_camera_and_source_error_is_explicit(self):
        status=self.fixture.train()
        run=status['run_id']; model=status['model']
        page=self.get('/quality/'+self.job+'/'+run).decode()
        self.assertIn('quality-reference',page)
        self.assertIn('review_frame',page)
        self.assertIn('camera_positions',json.dumps(status['quality']))
        dataset=self.fixture.directory/'model_runs'/run/'dataset'
        frame=app.read_json(dataset/'training_input/transforms_val.json')['frames'][0]
        # Give the fixture complete pinhole intrinsics for the browser camera.
        meta=app.read_json(dataset/'transforms.json');meta['fl_x']=16
        app.write_json(dataset/'transforms.json',meta)
        name=Path(frame['file_path']).stem
        viewer=self.get('/model/'+model+'?'+urlencode(dict(review_frame=name,variant='full'))).decode()
        self.assertIn('"review_camera"',viewer)
        self.assertIn('"name": "'+name+'"',viewer)
        with self.assertRaises(HTTPError) as error:
            self.get('/api/quality-source/'+self.job+'/'+run+'?frame='+name)
        self.assertEqual(error.exception.code,422)


if __name__=='__main__':unittest.main()
