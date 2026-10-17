"""Real isolated child processes: graceful stop, token fencing and parent death."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen, Request
from urllib.parse import urlencode

import task_control as control


def wait_for(predicate, timeout=15):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for test worker')


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # The filename is deliberately mapper, exercising production's native
        # signal route without running another reconstruction or using the GPU.
        (self.root/'mapper').write_text('''import signal,time
from pathlib import Path
def stop(*args):
    time.sleep(.3)
    Path('saved').write_text('complete')
    raise SystemExit(0)
signal.signal(signal.SIGINT,stop)
Path('ready').touch()
while True: time.sleep(.05)
''')

    def test_graceful_stop_preserves_save_and_does_not_signal_parent(self):
        errors=[]
        def run():
            try:
                with control.scope(self.root,'current'), (self.root/'native.log').open('w') as log:
                    control.run([sys.executable,'mapper'],cwd=self.root,stdout=log,stderr=log,check=True)
            except control.TaskStopped:
                errors.append('stopped')
            except Exception as exc:
                errors.append(str(exc))
        thread=threading.Thread(target=run);thread.start()
        try:
            wait_for(lambda:(self.root/'ready').exists())
            self.assertTrue(control.child_active(self.root))
            # A stop left over from an earlier run cannot interrupt this child.
            control.write(self.root/'.stop_request.json',{'token':'previous'})
            time.sleep(.3)
            self.assertFalse((self.root/'saved').exists())
            with self.assertRaises(ValueError):control.request_stop(self.root,'previous')
            control.request_stop(self.root,'current')
            self.assertTrue(control.controls(self.root,'current')['stop_requested'])
        finally:
            control.write(self.root/'.stop_request.json',{'token':'current'})
            thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,['stopped'])
        self.assertEqual((self.root/'saved').read_text(),'complete')
        self.assertFalse(control.child_active(self.root))

    def test_parent_death_requests_save_without_leaving_child_running(self):
        script = '''import sys
from pathlib import Path
import task_control as c
root=Path(sys.argv[1])
with c.scope(root,'orphan'),(root/'native.log').open('w') as log:
 c.run([sys.executable,'mapper'],cwd=root,stdout=log,stderr=log,check=True)
'''
        owner=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c',script,str(self.root)],cwd=Path(__file__).resolve().parents[1])
        try:
            wait_for(lambda:(self.root/'ready').exists() and control.child_active(self.root))
            owner.terminate();owner.wait(timeout=5)
            wait_for(lambda:(self.root/'saved').exists())
            wait_for(lambda:not control.child_active(self.root))
        finally:
            if owner.poll() is None:
                control.write(self.root/'.stop_request.json',{'token':'orphan'})
                owner.wait(timeout=15)
        self.assertEqual((self.root/'saved').read_text(),'complete')

    def test_feature_stage_finishes_before_stop_and_can_commit_cache(self):
        (self.root/'feature_extractor').write_text("import time\nfrom pathlib import Path\nPath('ready').touch()\ntime.sleep(.5)\nPath('complete').touch()\n")
        outcomes=[]
        def run():
            with control.scope(self.root,'features'),(self.root/'native.log').open('w') as log:
                result=control.run([sys.executable,'feature_extractor'],cwd=self.root,stdout=log,stderr=log,check=True)
                outcomes.append(result.returncode)
                # Pipeline can now mark the DB stage complete, before honoring Stop.
                try:control.check_stop()
                except control.TaskStopped:outcomes.append('stopped')
        thread=threading.Thread(target=run);thread.start()
        try:
            wait_for(lambda:(self.root/'ready').exists())
            control.request_stop(self.root,'features')
        finally:thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertTrue((self.root/'complete').exists())
        self.assertEqual(outcomes,[0,'stopped'])
        self.assertNotIn('signal_sent_at',control.read(self.root/'.controlled_process.json'))

    def test_identity_rejects_dead_pid_and_boundary_stop_is_fenced(self):
        self.assertIsNotNone(control.identity(os.getpid()))
        self.assertIsNone(control.identity(4294967294))
        with control.scope(self.root,'new'):
            control.write(self.root/'.stop_request.json',{'token':'old'})
            control.check_stop()
            control.request_stop(self.root,'new')
            with self.assertRaises(control.TaskStopped):control.check_stop()
        control.check_stop()


class StopHTTPTests(unittest.TestCase):
    def test_stop_action_is_bound_to_the_current_job_token(self):
        import test_task_history
        import web_app as app
        fixture=test_task_history.HistoryTests();fixture.setUp()
        self.addCleanup(fixture.tearDown)
        app.write_json(fixture.directory/'3d_status.json',{'job':fixture.job,'state':'running','stage':'model','progress':29})
        self.assertTrue(app.claim_task(fixture.job))
        self.addCleanup(app.release_task,fixture.job)
        token=app.read_json(fixture.directory/'.task.lock')['token']
        with control.scope(fixture.directory,token):pass
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base+'/api/job/'+fixture.job) as response:status=json.load(response)
            self.assertTrue(status['can_stop'])
            self.assertEqual(status['stop_token'],token)
            for value in ('obsolete',token,token):
                request=Request(base+'/actions',data=urlencode({'action':'stop_task','job':fixture.job,'token':value}).encode())
                with urlopen(request) as response:
                    self.assertEqual('level=error' in response.url,value=='obsolete')
                self.assertEqual(control.requested(fixture.directory,token),value!='obsolete')
            with urlopen(base+'/api/job/'+fixture.job) as response:status=json.load(response)
            self.assertTrue(status['stop_requested'])
            self.assertEqual(app.read_json(fixture.directory/'3d_status.json')['state'],'running')
        finally:
            server.shutdown();server.server_close();thread.join(5)

    def test_orphan_worker_keeps_job_and_queue_ticket_owned(self):
        import web_app as app
        with tempfile.TemporaryDirectory() as temp:
            jobs=Path(temp)/'jobs';job=jobs/'example';job.mkdir(parents=True)
            ticket=Path(temp)/'ticket.json'
            control.write(ticket,{'pid':4294967294,'job_directory':str(job)})
            with patch.object(app,'JOBS',jobs),patch.object(control,'child_active',return_value=True):
                self.assertTrue(app.task_lock_alive(ticket))
                self.assertTrue(app.task_is_active('example'))
                self.assertFalse(app.claim_task('example'))


if __name__=='__main__':unittest.main()
