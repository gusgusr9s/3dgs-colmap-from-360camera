"""Backend failures, atomic masks, and image contracts; hardware opt-in only."""
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import closing

import cv2
import numpy as np
import torch

from app.runtime import compute_runtime as runtime
from app.runtime.gpu_projection import WGPUProjector, GPUUnavailable, create_projector
from app.runtime.mask_compute import MaskPredictor, mask_mode
from app.pipeline.pano_to_views import perspective_map, project_frame
from app.runtime.view_compute import gpu_worker_views, view_mode


class BackendRecoveryTests(unittest.TestCase):
    def test_invalid_modes_are_rejected_before_dispatch(self):
        for function in (mask_mode,view_mode):
            with self.assertRaises(ValueError):function('unrecognized')
        self.assertEqual(mask_mode('directml:1'),'directml:1')

    def test_missing_wgpu_auto_falls_back_and_forced_mode_fails(self):
        events=[]
        with patch('app.runtime.view_compute.importlib.util.find_spec',return_value=None):
            self.assertIsNone(gpu_worker_views('missing', '.',{},compute=events.append,requested='auto'))
            with self.assertRaises(GPUUnavailable):gpu_worker_views('missing','.',{},requested='wgpu')
        self.assertEqual(events[-1]['backend'],'CPU')
        self.assertIn('not installed',events[-1]['fallback_reasons'][0])

    def test_cpu_mode_does_not_probe_gpu_or_spawn(self):
        with patch('app.runtime.view_compute.importlib.util.find_spec',side_effect=AssertionError('GPU queried')):
            self.assertIsNone(gpu_worker_views('missing','.',{},requested='cpu'))

    def test_projection_allocation_failure_halves_batch(self):
        attempts=[]
        def allocate(*args,**kwargs):
            attempts.append(kwargs['batch_limit'])
            if kwargs['batch_limit']>4:raise GPUUnavailable('out of memory')
            return object()
        with patch('app.runtime.gpu_projection.WGPUProjector',side_effect=allocate):
            _,reasons=create_projector(10,10,(10,20,3),[])
        self.assertEqual(attempts,[16,8,4]);self.assertEqual(len(reasons),2)

    def test_projection_driver_error_does_not_repeat_memory_retries(self):
        with patch('app.runtime.gpu_projection.WGPUProjector',side_effect=GPUUnavailable('device lost')) as allocate:
            with self.assertRaises(GPUUnavailable):create_projector(10,10,(10,20,3),[])
        self.assertEqual(allocate.call_count,1)

    def test_view_worker_tries_second_gpu_before_cpu(self):
        calls=[];events=[]
        class Process:
            def __init__(self,code,output):self.code=code;self.returncode=code;self.stdout=io.StringIO(output)
            def wait(self,**kwargs):return self.code
            def poll(self):return self.code
        with tempfile.TemporaryDirectory() as directory:
            def spawn(cmd,**kwargs):
                calls.append(kwargs['env']['WGPU_BACKEND_TYPE'])
                if len(calls)==1:return Process(82,'@views:{"gpu_error":"driver unavailable"}\n')
                Path(directory,'transforms.json').write_text('{"view_compute":{"backend":"WGPU/D3D12","device":"GPU"}}')
                return Process(0,'@views:{"complete":16}\n')
            with patch('app.runtime.view_compute.os',SimpleNamespace(name='nt',environ=dict(os.environ))),patch('app.runtime.view_compute.subprocess.Popen',side_effect=spawn),patch('app.runtime.view_compute.importlib.util.find_spec',return_value=True):
                self.assertEqual(gpu_worker_views('input',directory,{},compute=events.append,requested='auto'),16)
            self.assertEqual(calls,['Vulkan','D3D12'])
            import json
            meta=json.loads(Path(directory,'transforms.json').read_text())
            self.assertIn('driver unavailable',meta['view_compute']['fallback_reasons'][0])

    def test_view_worker_io_error_is_not_a_gpu_fallback(self):
        class Process:
            stdout=io.StringIO('@views:{"error":"Cannot write generated view"}\n')
            returncode=1
            def wait(self,**kwargs):return 1
            def poll(self):return 1
        with tempfile.TemporaryDirectory() as directory,patch('app.runtime.view_compute.subprocess.Popen',return_value=Process()) as spawn,patch('app.runtime.view_compute.importlib.util.find_spec',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'Cannot write'):gpu_worker_views('input',directory,{},requested='auto')
            self.assertEqual(spawn.call_count,1)

    def test_failed_mask_batch_preserves_previous_masks_and_cleans_staging(self):
        from app.pipeline import spherical_to_brush as spherical
        import torchvision.models.segmentation as segmentation
        from test_cpu_regression import make_mask_fixture,mask_weights,DeterministicPersonModel
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);make_mask_fixture(root)
            (root/'masks').mkdir();sentinel=root/'masks'/'keep.png';sentinel.write_bytes(b'previous masks')
            with patch.dict(os.environ,{'MASK_DEVICE':'cpu'}),patch.object(segmentation,'LRASPP_MobileNet_V3_Large_Weights',mask_weights()),patch.object(segmentation,'lraspp_mobilenet_v3_large',return_value=DeterministicPersonModel()),patch('app.runtime.mask_compute.MaskPredictor.probabilities',side_effect=ValueError('Invalid image shape')):
                with self.assertRaisesRegex(ValueError,'image shape'):spherical.create_dynamic_person_masks(root,_gpu_worker=True)
            self.assertEqual(sentinel.read_bytes(),b'previous masks')
            self.assertFalse(list(root.glob('masks-*.partial')))

    def test_software_wgpu_adapter_is_rejected(self):
        import types
        fake=types.SimpleNamespace(gpu=types.SimpleNamespace(request_adapter_sync=lambda **kw:types.SimpleNamespace(info={'adapter_type':'CPU'})))
        with patch.dict('sys.modules',{'wgpu':fake}):
            with self.assertRaisesRegex(GPUUnavailable,'hardware'):WGPUProjector(2,2,(2,4,3),[])

    def test_mask_inference_runtime_cuda_failure_retries_cpu(self):
        class Model(torch.nn.Module):
            def forward(self, inputs):return {'out':torch.zeros((len(inputs),21,2,2))}
        predictor=MaskPredictor(Model(),'cpu')
        predictor.mode='auto';predictor.backend='CUDA';predictor.candidates=['cpu']
        predictor.device=torch.device('cuda')
        result=predictor.probabilities(torch.zeros(1,3,2,2))
        self.assertEqual(result.shape,(1,2,2))
        self.assertEqual(predictor.backend,'CPU')
        self.assertTrue(predictor.reasons)

    def test_mask_does_not_hide_programming_or_data_errors(self):
        class Model(torch.nn.Module):
            def forward(self, inputs):raise ValueError('Invalid image shape')
        predictor=MaskPredictor(Model(),'cpu')
        with self.assertRaisesRegex(ValueError,'image shape'):predictor.probabilities(torch.zeros(1,3,2,2))

    def test_mask_oom_splits_batch_before_abandoning_gpu(self):
        calls=[]
        class Session:
            def run(self,names,values):
                size=len(values['images']);calls.append(size)
                if size>1:raise RuntimeError('DirectML out of memory')
                return [np.zeros((size,21,2,2),dtype=np.float32)]
        predictor=MaskPredictor(torch.nn.Identity(),'cpu')
        predictor.mode='auto';predictor.backend='DirectML';predictor.session=Session()
        result=predictor.probabilities(torch.zeros(4,3,2,2))
        self.assertEqual(result.shape,(4,2,2));self.assertEqual(predictor.backend,'DirectML')
        self.assertEqual(calls,[4,2,1,1,2,1,1])

    def test_native_cuda_mask_crash_tries_directml_before_cpu(self):
        import json
        from app.runtime import mask_compute
        class Process:
            def __init__(self,code,output):self.returncode=code;self.stdout=io.StringIO(output)
            def wait(self,**kwargs):return self.returncode
            def poll(self):return self.returncode
        result={'mask_compute':{'backend':'DirectML','device':'GPU','requested':'directml','fallback_reasons':[]}}
        retries=[]
        def spawn(command,**kwargs):
            retries.append(kwargs['env']['MASK_DEVICE'])
            if len(retries)==1:return Process(3221225477,'@masks:{"progress":{"done":0,"total":2,"compute":{"backend":"CUDA"}}}\n')
            return Process(0,'@masks:'+json.dumps({'complete':result})+'\n')
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'MASK_DEVICE':'auto'}),patch.object(mask_compute,'os',SimpleNamespace(name='nt',environ=dict(os.environ))),patch.object(mask_compute.subprocess,'Popen',side_effect=spawn):
            value=mask_compute.worker_masks(Path(directory))
        self.assertEqual(retries,['auto','directml']);self.assertEqual(value['mask_compute']['requested'],'auto')
        self.assertTrue(value['mask_compute']['fallback_reasons'])

    def test_stalled_worker_is_terminated_and_can_fallback(self):
        import sys
        from app.runtime.gpu_worker_io import worker_lines
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'],stdout=subprocess.PIPE,text=True)
        with self.assertRaisesRegex(GPUUnavailable,'timed out'):
            with worker_lines(process,timeout=.05) as lines:list(lines)
        self.assertIsNotNone(process.poll())

    def test_views_eta_restarts_rate_after_backend_change(self):
        from datetime import datetime,timezone
        from app.jobs.task_estimates import estimate_status
        status={'state':'running','stage':'views','started_at':'2026-10-13T10:00:00Z',
                'compute_changed_at':'2026-10-13T10:01:00Z','frames':16,'capture_plan':{'total_views':32,'views_per_frame':8}}
        estimate=estimate_status(status,now=datetime(2026,10,13,10,1,4,tzinfo=timezone.utc))
        self.assertEqual(estimate['eta_seconds'],4)

    def test_history_does_not_mix_mask_backends(self):
        from app.jobs.task_estimates import compatible_history
        status={'quality':{'mask_compute':{'backend':'DirectML','device':'RX'}}}
        old={'state':'done','elapsed_seconds':10,'quality':{'mask_compute':{'backend':'CPU','device':'CPU'}}}
        same={'state':'done','elapsed_seconds':10,'quality':status['quality']}
        self.assertEqual(compatible_history(status,[old,same]),[same])

    def colmap_failure(self, gpu_error, mode='auto', existing=False):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'database.db'
            if existing:
                with closing(sqlite3.connect(db)) as connection, connection:
                    connection.execute('create table keep(value text)');connection.execute("insert into keep values('previous stage')")
            command=['colmap','exhaustive_matcher' if existing else 'feature_extractor','--database_path',str(db),
                     '--FeatureMatching.use_gpu' if existing else '--FeatureExtraction.use_gpu','1']
            compute=runtime.ColmapCompute('CUDA','-1',True,'detected')
            log=io.StringIO();calls=[]
            def run(cmd,**kw):
                calls.append(cmd)
                if len(calls)==1:
                    with closing(sqlite3.connect(db)) as connection, connection:
                        connection.execute('create table partial(value text)')
                    kw['stdout'].write(gpu_error+'\n')
                    raise subprocess.CalledProcessError(1,cmd)
                if existing:
                    with closing(sqlite3.connect(db)) as connection:
                        self.assertEqual(connection.execute('select value from keep').fetchone()[0],'previous stage')
                        self.assertFalse(connection.execute("select name from sqlite_master where name='partial'").fetchall())
                else:self.assertFalse(db.exists())
            with patch.object(runtime.subprocess,'run',side_effect=run):
                selected=runtime.run_colmap_command(command,compute,log,{'COLMAP_DEVICE':mode,'CPU_THREADS':'3'},Path(directory))
            self.assertEqual(selected.backend,'CPU');self.assertTrue(selected.fallback_reasons)
            prefix='FeatureMatching' if existing else 'FeatureExtraction'
            self.assertEqual(calls[-1][calls[-1].index('--'+prefix+'.use_gpu')+1],'0')
            self.assertEqual(calls[-1][calls[-1].index('--'+prefix+'.num_threads')+1],'3')
            self.assertFalse(list(Path(directory).glob('*.backup')))

    def test_colmap_failed_extraction_removes_partial_database(self):self.colmap_failure('CUDA out of memory')
    def test_colmap_failed_matching_restores_completed_stage(self):self.colmap_failure('CUDA device lost',existing=True)
    def test_colmap_data_error_does_not_fallback(self):
        with self.assertRaises(subprocess.CalledProcessError):self.colmap_failure('Input directory not found')
    def test_colmap_forced_cuda_error_stays_visible(self):
        with self.assertRaises(subprocess.CalledProcessError):self.colmap_failure('CUDA device lost',mode='cuda')


@unittest.skipUnless(os.environ.get('RUN_GPU_TESTS')=='1','set RUN_GPU_TESTS=1 on an idle GPU machine')
class RealProjectionTests(unittest.TestCase):
    def test_random_pixels_match_opencv_at_seams_and_vertical_angles(self):
        image=np.random.default_rng(17).integers(0,256,(128,256,3),dtype=np.uint8)
        angles=[(0,0),(179,0),(-180,0),(25,55),(35,-55)]
        maps=[perspective_map(96,96,90,y,p)[:2] for y,p in angles]
        gpu=WGPUProjector(96,96,image.shape,maps)
        try:
            gpu.upload(image)
            for i,(y,p) in enumerate(angles):
                np.testing.assert_array_equal(gpu.project(i),project_frame(image,96,96,90,y,p,maps=maps[i]))
        finally:gpu.close()
