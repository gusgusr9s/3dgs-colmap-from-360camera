"""Desktop isolation, authenticated lifecycle and worker routing; no GPU workload."""
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.desktop.gateway import DesktopGateway
from app.runtime.python_command import module_command
from app.web import server as app


class DesktopGatewayTests(unittest.TestCase):
    def setUp(self):
        import test_task_history
        self.fixture=test_task_history.HistoryTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.guard=DesktopGateway('a'*64,'b'*32,self.fixture.root)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);self.server.desktop_gateway=self.guard
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path='/api/desktop/health',method='GET',headers=None):
        connection=HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        connection.request(method,path,headers=headers or {})
        response=connection.getresponse();result=(response.status,json.loads(response.read()))
        connection.close();return result

    def test_authentication_and_origin_fence(self):
        self.assertEqual(self.request()[0],403)
        headers={'X-Studio-Token':'a'*64}
        self.assertEqual(self.request(headers=headers)[0],200)
        self.assertEqual(self.request(method='POST',path='/api/desktop/prepare-quit',headers={**headers,'Origin':'https://foreign.test'})[0],403)
        cookie={'Cookie':self.guard.cookie+'='+'a'*64}
        self.assertEqual(self.request(headers=cookie)[0],200)
        self.assertEqual(self.request(method='POST',path='/api/desktop/prepare-quit',headers=cookie)[0],403)

    def test_drain_rejects_new_mutations_and_busy_upload_blocks_shutdown(self):
        headers={'X-Studio-Token':'a'*64}
        with self.guard.mutation() as allowed:
            self.assertTrue(allowed)
            code,status=self.request('/api/desktop/prepare-quit','POST',headers)
            self.assertEqual(code,409);self.assertTrue(status['busy']);self.assertFalse(status['draining'])
        code,status=self.request('/api/desktop/prepare-quit','POST',headers)
        self.assertEqual(code,200);self.assertTrue(status['draining'])
        self.assertEqual(self.request('/api/runtime-verification','POST',headers)[0],423)
        self.assertEqual(self.request('/api/desktop/resume','POST',headers)[0],200)

    def test_active_native_or_queued_job_blocks_quit(self):
        with patch.object(app,'task_is_active',return_value=True):
            self.assertTrue(self.guard.prepare_quit(app)['busy'])
            self.assertFalse(self.guard.draining)


class DesktopPathsTests(unittest.TestCase):
    def test_default_paths_and_external_workspace_are_independent(self):
        from app.paths import ROOT,WORKSPACE
        self.assertEqual(ROOT,WORKSPACE)
        with tempfile.TemporaryDirectory() as temp:
            import os
            command=[sys.executable,'-c','import json;from app.paths import ROOT,WORKSPACE;print(json.dumps([str(ROOT),str(WORKSPACE)]))']
            result=subprocess.run(command,cwd=ROOT,env={**os.environ,'STUDIO_WORKSPACE':temp},capture_output=True,text=True,check=True)
            resource,workspace=json.loads(result.stdout)
            self.assertEqual(Path(resource),ROOT);self.assertEqual(Path(workspace),Path(temp).resolve())

    def test_worker_command_uses_frozen_dispatch_only_when_packaged(self):
        with patch.object(sys,'frozen',False,create=True):
            self.assertEqual(module_command('app.runtime.verify_cli','--json')[1:6],['-u','-X','utf8','-m','app.runtime.verify_cli'])
        with patch.object(sys,'frozen',True,create=True):
            self.assertEqual(module_command('app.runtime.verify_cli','--json')[1:3],['--worker-module','app.runtime.verify_cli'])


if __name__=='__main__':unittest.main()
