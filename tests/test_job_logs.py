"""The live console must remain bounded, read-only and useful for old running jobs."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import job_logs
import web_app as app


class LogSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = self.root / 'example'; self.job.mkdir()

    def status(self, name, value):
        (self.job / name).write_text(json.dumps(value), encoding='utf8')

    def model(self, text='Registering image #5 (num_reg_frames=4)\n'):
        run = self.job / 'model_runs' / 'run1'; run.mkdir(parents=True)
        path = run / '3d.log'; path.write_text(text, encoding='utf8')
        self.status('3d_status.json', {'state':'running','run_id':'run1','stage':'model','step':'Finding poses'})
        return path

    def test_legacy_native_log_updates_and_unchanged_response_does_not_reread(self):
        path = self.model(); before = path.stat()
        first = job_logs.snapshot(self.job)
        self.assertEqual(first['source'], 'model')
        self.assertIn('num_reg_frames=4', first['text'])
        second = job_logs.snapshot(self.job, cursor=first['cursor'])
        self.assertTrue(second['unchanged']); self.assertEqual(second['text'], '')
        with path.open('a', encoding='utf8') as handle: handle.write('Global bundle adjustment\n')
        third = job_logs.snapshot(self.job, cursor=first['cursor'])
        self.assertFalse(third['unchanged']); self.assertIn('Global bundle adjustment', third['text'])
        self.assertNotEqual(before.st_size, path.stat().st_size)
        self.assertFalse((path.parent / 'process_summary.json').exists())

    def test_bounded_utf8_tail_and_ansi_and_rotation(self):
        path = self.model(('ภาษาไทย ' + 'x'*400 + '\n') * 2000 + '\x1b[31mWARNING\x1b[0m\rstep 10\n')
        stat = path.stat(); first = job_logs.snapshot(self.job)
        self.assertTrue(first['truncated'])
        self.assertLessEqual(len(first['text'].encode('utf8')), job_logs.MAX_BYTES)
        self.assertLessEqual(len(first['text'].splitlines()), job_logs.MAX_LINES)
        self.assertNotIn('\ufffd', first['text']); self.assertNotIn('\x1b', first['text'])
        self.assertTrue(first['text'].endswith('step 10'))
        self.assertEqual(path.stat().st_mtime_ns, stat.st_mtime_ns)
        path.write_text('replacement\n', encoding='utf8')
        self.assertEqual(job_logs.snapshot(self.job, cursor=first['cursor'])['text'], 'replacement')

    def test_auto_follows_views_then_model_and_manual_source_remains_available(self):
        path = self.model()
        view = self.job / 'view_runs' / 'views1'; view.mkdir(parents=True)
        (view / 'process.log').write_text('Generated 12 views\n', encoding='utf8')
        self.status('views_status.json', {'state':'running','run_id':'views1','stage':'views'})
        self.assertEqual(job_logs.snapshot(self.job)['source'], 'views')
        self.status('views_status.json', {'state':'ready','run_id':'views1','stage':'views'})
        self.assertEqual(job_logs.snapshot(self.job)['source'], 'model')
        self.assertEqual(job_logs.snapshot(self.job, 'views')['text'], 'Generated 12 views')
        self.status('3d_status.json', {'state':'running','run_id':'new-run','stage':'model'})
        self.assertIsNone(job_logs.snapshot(self.job)['source'])
        self.assertTrue(path.is_file())

    def test_pose_only_and_empty_logs_and_line_limit(self):
        self.status('pose_status.json', {'state':'running','stage':'poses'})
        path = self.job / 'poses.log'; path.touch()
        self.assertEqual(job_logs.snapshot(self.job)['source'], 'poses')
        self.assertEqual(job_logs.snapshot(self.job)['text'], '')
        path.write_text('\n'.join(str(i) for i in range(1000)), encoding='utf8')
        result = job_logs.snapshot(self.job)
        self.assertEqual(len(result['text'].splitlines()), 300)
        self.assertTrue(result['truncated']); self.assertTrue(result['text'].endswith('999'))

    def test_auto_uses_mask_progress_journal_then_native_training_output(self):
        path = self.model()
        (path.parent / 'process.log').write_text('Masking people 12/48\n', encoding='utf8')
        self.status('3d_status.json', {'state':'running','run_id':'run1','phase':'masks','step':'Masking people 12/48'})
        self.assertEqual(job_logs.snapshot(self.job)['source'], 'model-process')
        self.status('3d_status.json', {'state':'running','run_id':'run1','phase':'training','step':'Training'})
        self.assertEqual(job_logs.snapshot(self.job)['source'], 'model')

    def test_sources_and_status_paths_cannot_escape_job(self):
        (self.root / '3d.log').write_text('SECRET', encoding='utf8')
        self.status('3d_status.json', {'state':'running','run_id':'../../..','stage':'model'})
        result = job_logs.snapshot(self.job)
        self.assertNotIn('SECRET', result['text']); self.assertIsNone(result['path'])
        with self.assertRaises(ValueError): job_logs.snapshot(self.job, '../3d.log')

    def test_http_endpoint_and_markup_do_not_mutate_or_start_processing(self):
        path = self.model('<script>bad()</script>\n'); before = path.stat()
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.object(app, 'JOBS', self.root), patch.object(app, 'start_3d') as start:
                base = f'http://127.0.0.1:{server.server_port}'
                with urlopen(base + '/api/job-log/example') as response:
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    result = json.load(response)
                self.assertIn('<script>', result['text'])  # Client uses textContent, never innerHTML.
                with self.assertRaises(HTTPError) as error: urlopen(base + '/api/job-log/missing')
                self.assertEqual(error.exception.code, 404)
                with self.assertRaises(HTTPError) as error: urlopen(base + '/api/job-log/example?source=invalid')
                self.assertEqual(error.exception.code, 400)
                start.assert_not_called()
                with patch.object(app, 'available_pose_preview', return_value={}):
                    pose = app.pose_preview_page('example')
                self.assertIn('data-live-log="example"', pose)
                self.assertNotIn('__LIVE_LOG__', pose)
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
