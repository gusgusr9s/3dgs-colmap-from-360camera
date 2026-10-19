import json
import os
from pathlib import Path
import tempfile
import threading
import time
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.jobs.processing_queue import processing_slot, try_lock
from app.jobs.task_control import write
from app.web import temporary_poses as preview
from app.web import server


class TemporaryPoseTests(unittest.TestCase):
    def test_manual_30_frame_sampling_can_use_ten_seconds_without_relaxing_minimum(self):
        settings={**self.settings,'sample_every':30,'sampling_guard':'manual'}
        with self.assertRaises(ValueError):
            preview.start(self.source,self.profile,settings,self.cache)
        with patch.object(preview.threading.Thread,'start',autospec=True,side_effect=lambda thread:thread._args[-1].close()):
            result=preview.start(self.source,self.profile,settings,self.cache,seconds=10)
        request=next((self.cache/result['id']).glob('attempt-*/request.json'))
        data=json.loads(request.read_text(encoding='utf8'))
        self.assertEqual(data['settings']['end_seconds'],10)
        self.assertEqual(data['settings']['sample_every'],30)
        self.assertEqual(data['settings']['sampling_guard'],'manual')
        with self.assertRaises(ValueError):
            preview.start(self.source,self.profile,settings,self.cache,seconds=999)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cache=self.root/'cache';self.queue=self.root/'queue'
        self.jobs=self.root/'jobs';self.jobs.mkdir()
        self.source=self.root/'source.mp4';self.source.write_bytes(b'immutable source')
        self.profile=dict(kind='video',fps=30,frame_count=300,duration_seconds=10,width=1920,height=960,aspect_ratio=2)
        self.settings={**server.DEFAULT_VIEW_SETTINGS,'projection':'panorama','sample_every':8,'start_seconds':0,'end_seconds':0}

    def test_preview_starts_during_train_and_deduplicates_without_creating_jobs(self):
        called=threading.Event(); finish=threading.Event()
        def worker(command,**kwargs):
            attempt=Path(command[-1]).parent
            request=json.loads((attempt/'request.json').read_text())
            self.assertEqual(request['settings']['end_seconds'],5)
            self.assertLessEqual(request['settings']['width'],640)
            self.assertEqual(request['settings']['quality'],'preview')
            for key in ('VIEW_DEVICE','COLMAP_DEVICE','COLMAP_BA_DEVICE','MASK_DEVICE'):
                self.assertEqual(kwargs['env'][key],'cpu')
            self.assertEqual(kwargs['env']['CPU_THREADS'],'2')
            self.assertEqual(kwargs['env']['OPENBLAS_NUM_THREADS'],'2')
            self.assertEqual(kwargs['env']['COLMAP_MATCHER'],'sequential')
            self.assertEqual(kwargs['timeout'],900)
            called.set()
            self.assertTrue(finish.wait(3))
            write(attempt.parent/'preview.json',dict(cameras=[{'position':[0,0,0]},{'position':[1,0,0]}],views=[]))
            write(attempt.parent/'status.json',dict(state='ready',at=time.time(),step='ready'))
            return SimpleNamespace(returncode=0)
        with patch.object(preview.subprocess,'run',side_effect=worker) as run:
            with processing_slot(self.queue,lambda _:True,poll_seconds=.01):
                result=preview.start(self.source,self.profile,self.settings,self.cache)
                self.assertTrue(called.wait(2),'Preview must start while Train holds the real queue lock')
                second=preview.start(self.source,self.profile,self.settings,self.cache)
                self.assertEqual(result['id'],second['id'])
                self.assertEqual(run.call_count,1)
                self.assertEqual(preview.status(self.cache,result['id'])['state'],'running')
                self.assertEqual(len(list(self.queue.glob('*.json'))),1,'Only the training ticket may use the queue')
                self.assertEqual(list(self.jobs.iterdir()),[])
                finish.set()
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                state=preview.status(self.cache,result['id'])
                if state['state']=='ready':break
                time.sleep(.01)
            self.assertEqual(state['state'],'ready')
            self.assertFalse(state['training_ready'])
            self.assertEqual(self.source.read_bytes(),b'immutable source')
            while time.monotonic()<deadline:
                with (self.cache/result['id']/'.lock').open('r+b') as lock:
                    if try_lock(lock):break
                time.sleep(.01)

    def test_cleanup_expires_images_but_never_removes_locked_preview_or_external_files(self):
        self.cache.mkdir()
        path=preview.directory(self.cache,'a'*32);path.mkdir()
        image=path/'image.jpg';image.write_bytes(b'image')
        external=self.root/'external.jpg';external.write_bytes(b'keep')
        write(path/'status.json',dict(state='ready',at=time.time()-preview.TTL_SECONDS-1))
        with (path/'.lock').open('a+b') as lock:
            lock.write(b'0');lock.flush();self.assertTrue(try_lock(lock))
            preview.cleanup(self.cache)
            self.assertTrue(image.exists())
        preview.cleanup(self.cache)
        self.assertFalse(image.exists())
        self.assertEqual(preview.status(self.cache,'a'*32)['state'],'expired')
        self.assertEqual(external.read_bytes(),b'keep')

    def test_file_routes_restrict_paths_types_and_expiry(self):
        path=preview.directory(self.cache,'b'*32);path.mkdir(parents=True)
        write(path/'status.json',dict(state='ready',at=time.time()))
        write(path/'preview.json',dict(cameras=[]))
        image=path/'image.jpg';image.write_bytes(b'jpeg')
        self.assertEqual(preview.image_file(self.cache,'b'*32,'image.jpg'),image)
        for name in ('../external.jpg','preview.json'):
            with self.assertRaises(ValueError):preview.image_file(self.cache,'b'*32,name)
        with self.assertRaises(ValueError):preview.directory(self.cache,'../jobs')
        write(path/'status.json',dict(state='ready',at=time.time()-preview.TTL_SECONDS-1))
        with self.assertRaises(ValueError):preview.image_file(self.cache,'b'*32,'image.jpg')

    def test_source_fingerprint_and_sampling_settings_separate_cache_entries(self):
        with patch.object(preview.threading,'Thread',side_effect=lambda target,args,daemon:SimpleNamespace(start=lambda:args[-1].close())):
            first=preview.start(self.source,self.profile,self.settings,self.cache)
            second=preview.start(self.source,self.profile,{**self.settings,'sample_every':4},self.cache)
            self.assertNotEqual(first['id'],second['id'])

    def test_different_previews_can_start_together_while_main_queue_is_locked(self):
        started=[]; both=threading.Event(); finish=threading.Event()
        def worker(command,**kwargs):
            started.append(command[-1])
            if len(started)==2:both.set()
            self.assertTrue(finish.wait(3))
            path=Path(command[-1]).parent.parent
            write(path/'preview.json',dict(cameras=[],views=[]))
            write(path/'status.json',dict(state='ready',at=time.time()))
            return SimpleNamespace(returncode=0)
        with patch.object(preview.subprocess,'run',side_effect=worker), processing_slot(self.queue,lambda _:True):
            first=preview.start(self.source,self.profile,self.settings,self.cache)
            second=preview.start(self.source,self.profile,{**self.settings,'sample_every':10},self.cache)
            try:
                self.assertTrue(both.wait(2),'Different previews must not wait for Train or each other')
                self.assertNotEqual(first['id'],second['id'])
                self.assertEqual(len(list(self.queue.glob('*.json'))),1)
            finally:
                finish.set()
            for identifier in (first['id'],second['id']):
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    with (self.cache/identifier/'.lock').open('r+b') as handle:
                        if try_lock(handle):break
                    time.sleep(.01)

    def test_worker_uses_private_outputs_and_never_calls_training_or_training_masks(self):
        from app.runtime import pose_preview_worker
        identifier='c'*32
        parent=preview.directory(self.cache,identifier);attempt=parent/'attempt-test';attempt.mkdir(parents=True)
        stamp=self.source.stat()
        request=attempt/'request.json'
        write(request,dict(source=str(self.source),size=stamp.st_size,mtime_ns=stamp.st_mtime_ns,settings=self.settings))
        def views(source,destination,**kwargs):
            self.assertTrue(destination.is_dir(),'Worker output directory must exist before opening Views logs')
            write(destination/'transforms.json',dict(frames=[]))
        data=dict(cameras=[{'position':[0,0,0]}],views=[dict(name='frame.jpg',image_url='/jobs/views/brush_data/images/frame.jpg')])
        with patch.dict(os.environ), patch.object(sys,'argv',['worker',str(request)]), \
             patch.object(server,'process_file',side_effect=views), \
             patch.object(server,'colmap_bin_dir',return_value=self.root), \
             patch.object(server,'run_spherical_colmap',return_value={'registered_panoramas':19}), \
             patch('app.pipeline.reconstruction_preview.build_pose_preview',return_value=data), \
             patch.object(server,'generate_3d') as training,patch.object(server,'create_dynamic_person_masks') as masks:
            pose_preview_worker.main()
            training.assert_not_called();masks.assert_not_called()
        record=json.loads((parent/'status.json').read_text(encoding='utf8'))
        result=json.loads((parent/'preview.json').read_text(encoding='utf8'))
        self.assertEqual(record['state'],'ready')
        self.assertTrue(result['views'][0]['image_url'].startswith('/pose-preview-files/'+identifier+'/'))
        self.assertEqual(list(self.jobs.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
