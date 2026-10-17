"""Clip alignment, actual JPEG projections, HTTP previews and automatic pipeline."""
import json
import math
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_app as app
from capture_preview import sampling_plan, preview_jpeg
import capture_preview
from view_suggestions import probe_source
from pano_to_views import frames_from_input


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=app.ROOT/'web_data', prefix='capture_check_')
        self.root = Path(self.tmp.name).resolve()
        assert self.root.is_relative_to((app.ROOT/'web_data').resolve())
        self.source = self.root/'source.mp4'
        writer=app.cv2.VideoWriter(str(self.source),app.cv2.VideoWriter_fourcc(*'mp4v'),8,(320,160))
        assert writer.isOpened()
        for i in range(24):
            image=app.np.zeros((160,320,3),app.np.uint8)
            image[:,:,0]=i*8;image[:,:,1]=app.np.arange(320,dtype=app.np.uint16)[None,:]%256
            image[:,:,2]=app.np.arange(160,dtype=app.np.uint8)[:,None]
            writer.write(image)
        writer.release()
        self.profile=probe_source(self.source)
        self.settings={**app.DEFAULT_VIEW_SETTINGS,'width':160,'height':160,'quality':'preview',
                       'sample_every':2,'start_seconds':.375,'end_seconds':1.625}

    def tearDown(self):
        self.tmp.cleanup()

    def test_sampling_anchors_to_selected_start_and_end_is_exclusive(self):
        plan=sampling_plan(self.profile,self.settings)
        self.assertEqual((plan['start_frame'],plan['end_frame'],plan['positions']),(3,13,5))
        frames=list(frames_from_input(self.source,2,3,13))
        self.assertEqual([index for index,_ in frames],[3,5,7,9,11])
        self.assertEqual(plan['total_views'],40)

    def test_preview_matches_real_generated_crop_and_spherical_frames(self):
        destination=self.root/'job';destination.mkdir()
        count=app.process_file(self.source,destination,**self.settings)
        self.assertEqual(count,40)
        meta=app.read_json(destination/'transforms.json')
        self.assertEqual(sorted({frame['frame_index'] for frame in meta['frames']}),[3,5,7,9,11])
        data,_,frame=preview_jpeg(self.source,self.profile,self.settings,ordinal=1,direction=3)
        decoded=app.cv2.imdecode(app.np.frombuffer(data,app.np.uint8),app.cv2.IMREAD_COLOR)
        generated=app.cv2.imread(str(destination/'frame_000005_view_03.jpg'))
        self.assertEqual(frame,5)
        self.assertLess(float(app.np.abs(decoded.astype(float)-generated.astype(float)).mean()),2)
        output=destination/'spherical_colmap'/'panoramas'
        self.assertTrue(output.resolve().is_relative_to(self.root))
        self.assertEqual(app.extract_spherical_keyframes(self.source,meta,output),5)
        self.assertEqual([p.name for p in sorted(output.glob('*.jpg'))],
                         [f'pano_{i:06}.jpg' for i in (3,5,7,9,11)])

    def test_invalid_ranges_and_nonfinite_values(self):
        for start,end in ((-1,1),(2,1),(3,0),(0,4),(math.nan,1),(0,math.inf),(.38,.39)):
            with self.assertRaises(ValueError):sampling_plan(self.profile,{**self.settings,'start_seconds':start,'end_seconds':end})

    def test_full_clip_and_temporal_limit(self):
        plan=sampling_plan(self.profile,{**self.settings,'start_seconds':0,'end_seconds':0,'sample_every':100})
        self.assertEqual(plan['effective_sample_every'],2)
        self.assertEqual(plan['positions'],12)

    def test_portrait_and_static_image(self):
        photo=self.root/'photo.png';app.cv2.imwrite(str(photo),app.np.zeros((300,160,3),app.np.uint8))
        profile=probe_source(photo)
        plan=sampling_plan(profile,{**self.settings,'projection':'auto','start_seconds':0,'end_seconds':0})
        self.assertFalse(plan['panorama']);self.assertEqual(plan['total_views'],1)
        with self.assertRaises(ValueError):sampling_plan(profile,self.settings)

    def test_preview_rejects_out_of_range_and_keeps_source(self):
        before=self.source.read_bytes()
        for ordinal,direction in ((5,0),(-1,0),(0,8),(0,-1)):
            with self.assertRaises(ValueError):preview_jpeg(self.source,self.profile,self.settings,ordinal,direction)
        preview_jpeg(self.source,self.profile,self.settings,raw=True)
        self.assertEqual(before,self.source.read_bytes())

    def test_preview_busy_rejects_without_waiting_for_model_queue_or_spawning(self):
        with patch.object(capture_preview,'preview_worker') as worker:
            self.assertTrue(capture_preview.DECODERS.acquire(timeout=.1))
            try:
                with self.assertRaises(capture_preview.PreviewBusy):
                    preview_jpeg(self.source,self.profile,self.settings)
            finally:
                capture_preview.DECODERS.release()
            worker.assert_not_called()

    def test_preview_timeout_is_bounded_and_source_untouched(self):
        original=self.source.stat()
        with patch.object(capture_preview.subprocess,'run',side_effect=subprocess.TimeoutExpired('preview',25)):
            with self.assertRaisesRegex(ValueError,'25'):
                capture_preview.preview_worker('frame',self.source,0)
        self.assertEqual(self.source.stat().st_size,original.st_size)
        self.assertEqual(self.source.stat().st_mtime_ns,original.st_mtime_ns)

    def test_preview_panorama_and_crop_share_one_bounded_decode(self):
        capture_preview.decoded_frame.cache_clear()
        with patch.object(capture_preview,'preview_worker',wraps=capture_preview.preview_worker) as worker:
            preview_jpeg(self.source,self.profile,self.settings,raw=True)
            preview_jpeg(self.source,self.profile,self.settings,direction=1)
        self.assertEqual(worker.call_count,1)

    def test_http_plan_and_image(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(app,'source_from_key',return_value=self.source):
                query=urlencode({'source':'uploads:source.mp4',**self.settings,'ordinal':1,'direction':3})
                with urlopen(base+'/api/capture-plan?'+query) as response:
                    self.assertEqual(json.load(response)['plan']['positions'],5)
                with urlopen(base+'/api/capture-image?'+query) as response:
                    self.assertEqual(response.headers['X-Source-Frame'],'5')
                    self.assertEqual(response.headers['Content-Type'],'image/jpeg')
                    self.assertTrue(response.read().startswith(b'\xff\xd8'))
                with self.assertRaises(HTTPError) as error:urlopen(base+'/api/capture-plan?'+urlencode({'source':'x','start_seconds':'nan'}))
                self.assertEqual(error.exception.code,422)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_auto_pipeline_keeps_lock_until_model_phase(self):
        jobs=self.root/'jobs';directory=jobs/'demo';directory.mkdir(parents=True)
        seen=[]
        def model(job):
            seen.append((job,app.task_is_active(job),app.views_status(job)['state']))
            app.release_task(job)
        with patch.object(app,'JOBS',jobs),patch.object(app,'source_from_key',return_value=self.source),patch.object(app,'generate_3d',side_effect=model):
            app.write_json(directory/'job.json',{'label':'auto'})
            self.assertTrue(app.claim_task('demo'))
            app.generate_views('demo','x',{**self.settings,'end_seconds':2.5},auto_model=True)
            self.assertEqual(seen,[('demo',True,'ready')])
            self.assertTrue(app.job_manifest('demo')['auto_model'])
            self.assertEqual(app.job_manifest('demo')['source_profile']['fingerprint'],self.profile['fingerprint'])
            self.assertFalse(app.task_is_active('demo'))

    def test_bad_clip_does_not_start_training(self):
        jobs=self.root/'jobs';directory=jobs/'demo';directory.mkdir(parents=True)
        with patch.object(app,'JOBS',jobs),patch.object(app,'source_from_key',return_value=self.source),patch.object(app,'generate_3d') as train:
            app.claim_task('demo');app.generate_views('demo','x',self.settings,auto_model=True)
            train.assert_not_called();self.assertEqual(app.views_status('demo')['state'],'error')
            self.assertFalse(app.task_is_active('demo'))

    def test_http_create_preserves_trim_and_automatic_choice(self):
        jobs=self.root/'jobs';models=self.root/'models';uploads=self.root/'uploads'
        for directory in (jobs,models,uploads):directory.mkdir()
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.multiple(app,JOBS=jobs,MODELS=models,UPLOADS=uploads,RAW=self.root),patch.object(app,'start_views',return_value=True) as start:
                data=urlencode({'action':'generate','source':'data/raw:source.mp4','label':'clip test',
                    **self.settings,'end_seconds':2.5,'auto_model':1}).encode()
                with urlopen(Request(f'http://127.0.0.1:{server.server_port}/actions',data=data)) as response:
                    self.assertIn('/job/',response.url)
                args=start.call_args
                self.assertTrue(args.kwargs['auto_model'])
                self.assertEqual(args.args[2]['start_seconds'],.375)
                self.assertEqual(args.args[2]['end_seconds'],2.5)
                manifest=app.read_json(next(jobs.glob('*/job.json')))
                self.assertEqual(manifest['settings']['sample_every'],2)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_changed_source_is_rejected_before_colmap(self):
        jobs=self.root/'jobs';directory=jobs/'demo';directory.mkdir(parents=True)
        app.process_file(self.source,directory,**self.settings)
        app.write_json(directory/'job.json',{'source_key':'x','settings':self.settings,
            'source_profile':{**self.profile,'fingerprint':'changed'}})
        with patch.object(app,'JOBS',jobs),patch.object(app,'source_from_key',return_value=self.source),patch.object(app.subprocess,'run') as subprocess_run:
            app.generate_3d('demo')
            self.assertEqual(subprocess_run.call_count,0)
            self.assertIn('ต้นฉบับเปลี่ยน',app.model_status('demo')['error'])


if __name__=='__main__':unittest.main()
