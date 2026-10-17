"""Readiness is an inventory, never a hidden training/inference smoke test."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_verification as verify
import verification_service as service
import web_app as app


class VerificationTests(unittest.TestCase):
    def inventory(self, env=None, gpu=None, masks=None):
        gpu = gpu if gpu is not None else {'hardware':[{'name':'AMD Radeon','index':0}],
              'adapters':[{'device':'AMD Radeon','backend_type':'Vulkan'}], 'errors':[]}
        masks = masks if masks is not None else {'torch':'1.0','cuda':[], 'directml':True, 'errors':[]}
        def banner(command, args):
            return 'COLMAP 4.2.1 without GPU support' if args == ['-h'] else 'tool version 1'
        with tempfile.TemporaryDirectory() as temp, patch.dict(verify.os.environ, env or {}, clear=True), \
             patch.object(verify,'gpu_inventory',return_value=gpu), patch.object(verify,'mask_inventory',return_value=masks), \
             patch.object(verify,'executable',return_value='tool'), patch.object(verify,'command_banner',side_effect=banner), \
             patch.object(verify,'colmap_executable',return_value='tool'), \
             patch.object(verify.metadata,'version',return_value='1.0'):
            return verify.verify_environment(temp)

    def test_amd_reports_independent_backends_and_no_cpu_training_claim(self):
        report=self.inventory()
        stages={s['key']:s for s in report['stages']}
        self.assertEqual(stages['colmap']['backend'],'CPU')
        self.assertIn('not built with CUDA',stages['colmap']['reason'])
        self.assertEqual(stages['masks']['backend'],'DirectML')
        self.assertIn('WGPU',stages['views']['backend'])
        self.assertEqual(stages['model']['state'],'warning')
        self.assertIn('ไม่มี CPU training fallback',stages['model']['reason'])
        self.assertIn('ไม่ทำ inference',report['scope'])

    def test_no_adapter_and_forced_gpu_is_error_but_auto_views_can_fallback(self):
        gpu={'hardware':[],'adapters':[],'errors':[]}
        masks={'cuda':[],'directml':False,'errors':[]}
        auto=self.inventory(gpu=gpu,masks=masks)
        stages={s['key']:s for s in auto['stages']}
        self.assertEqual(stages['views']['backend'],'CPU')
        self.assertEqual(stages['masks']['backend'],'CPU')
        self.assertFalse(auto['ready'])
        forced=self.inventory({'VIEW_DEVICE':'wgpu','MASK_DEVICE':'cuda'},gpu,masks)
        stages={s['key']:s for s in forced['stages']}
        self.assertEqual(stages['views']['state'],'error')
        self.assertEqual(stages['masks']['state'],'error')

    def test_command_probe_is_bounded_and_never_invokes_processing(self):
        with patch.object(verify.Path,'is_file',return_value=True), \
             patch.object(verify.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='version',stderr='')) as run:
            verify.command_banner('brush_app',['--version'])
        self.assertEqual(run.call_args.args[0],['brush_app','--version'])
        self.assertEqual(run.call_args.kwargs['timeout'],10)

    def test_cuda_inventory_never_initializes_torch_cuda_context(self):
        fake=SimpleNamespace(__version__='test+cuda',version=SimpleNamespace(cuda='12.8'))
        ort=SimpleNamespace(get_available_providers=lambda:[])
        # No fake.cuda attribute exists: any CUDA runtime / tensor call would fail.
        with patch.dict(sys.modules,{'torch':fake,'onnxruntime':ort}), \
             patch.dict(verify.os.environ,{},clear=True), patch.object(verify,'nvidia_devices',return_value=['RTX']):
            result=verify.mask_inventory()
        self.assertEqual(result['cuda'],['RTX'])
        self.assertEqual(result['errors'],[])
        self.assertIn('context',result['cuda_detection'])

    def test_web_get_does_not_start_and_post_starts_one_check(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            endpoint=f'http://127.0.0.1:{server.server_port}/api/runtime-verification'
            with patch.object(service,'start') as start, patch.object(service,'status',return_value={'state':'idle'}):
                with urlopen(endpoint) as response:self.assertEqual(json.load(response)['state'],'idle')
                start.assert_not_called()
                with urlopen(Request(endpoint,data=b'',method='POST')) as response:self.assertEqual(response.status,202)
                start.assert_called_once_with(app.UPLOADS.parent)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_duplicate_check_is_coalesced(self):
        old=service.status()
        try:
            with patch.object(service.threading,'Thread') as thread:
                self.assertTrue(service.start('workspace'))
                self.assertFalse(service.start('workspace'))
                self.assertEqual(thread.call_count,1)
        finally:
            with service._lock:
                service._state.clear();service._state.update(old)

    def test_verifier_timeout_is_recoverable(self):
        old=service.status()
        try:
            with patch.object(service.subprocess,'run',side_effect=subprocess.TimeoutExpired('verify',70)):
                service._run('workspace')
            self.assertEqual(service.status()['state'],'error')
            self.assertIn('70',service.status()['error'])
        finally:
            with service._lock:
                service._state.clear();service._state.update(old)


if __name__ == '__main__':unittest.main()
