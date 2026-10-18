"""ETA accuracy boundaries, retries, completion, phase timing and live APIs."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import server as app
from app.jobs.task_estimates import estimate_status, training_samples, PhaseTimer

NOW = datetime(2026, 10, 13, 14, 0, 0, tzinfo=timezone.utc)


def logs(rows):
    return '\n'.join(f'[{at} INFO brush_cli] Refine iter {iteration}, 100 splats.' for at, iteration in rows)


class EstimateTests(unittest.TestCase):
    def test_view_finish_uses_actual_view_count_not_percent(self):
        status = dict(state='running', stage='views', started_at='2026-10-13T13:59:00Z',
                      frames=100, progress=99, capture_plan=dict(total_views=400, views_per_frame=16))
        result = estimate_status(status, now=NOW)
        self.assertEqual(result['eta_seconds'], 180)
        self.assertEqual(result['estimated_finish_at'], '2026-10-13T14:03:00+00:00')
        self.assertEqual(status['progress'], 99)
        self.assertNotIn('eta_seconds', status)

    def test_warmup_empty_and_completed_are_honest(self):
        base = dict(state='running', stage='views', started_at='2026-10-13T13:59:00Z', capture_plan=dict(total_views=400, views_per_frame=16))
        for done in (0, 16):
            result = estimate_status({**base, 'frames':done}, now=NOW)
            self.assertEqual(result['estimate_state'], 'warming_up')
            self.assertNotIn('eta_seconds', result)
        self.assertEqual(estimate_status({**base, 'frames':400}, now=NOW)['estimate_state'], 'finishing')
        for state in ('done', 'ready', 'error', 'queued'):
            result = estimate_status({**base, 'state':state, 'eta_seconds':100, 'estimated_finish_at':'old'}, now=NOW)
            self.assertNotIn('eta_seconds', result)
            self.assertNotIn('estimated_finish_at', result)

    def test_training_uses_recent_steps_not_prep_time(self):
        status = dict(state='running', stage='model', progress=40, training_steps=2000, started_at='2026-10-13T13:00:00Z')
        log = logs([('2026-10-13T13:59:30Z',400), ('2026-10-13T13:59:40Z',600), ('2026-10-13T13:59:50Z',800)])
        result = estimate_status(status, now=NOW, log=log)
        self.assertEqual(result['eta_training_seconds'], 50)
        self.assertEqual(result['eta_seconds'], 110)
        self.assertEqual(result['eta_finalization_source'], 'initial_allowance')

    def test_stalled_training_does_not_keep_counting_down(self):
        status = dict(state='running', stage='model', progress=40, training_steps=2000)
        log = logs([('2026-10-13T13:50:00Z',400), ('2026-10-13T13:50:10Z',600)])
        result = estimate_status(status, now=NOW, log=log)
        self.assertEqual(result['estimate_state'], 'stale')
        self.assertNotIn('eta_seconds', result)

    def test_retry_discards_previous_training_speed(self):
        log = logs([('2026-10-13T13:58:00Z',400), ('2026-10-13T13:58:01Z',600),
                    ('2026-10-13T13:59:40Z',200), ('2026-10-13T13:59:50Z',400)])
        self.assertEqual([step for _,step in training_samples(log)], [200,400])

    def test_first_model_prep_waits_and_history_is_config_specific(self):
        status = dict(state='running', stage='model', progress=29, started_at='2026-10-13T13:59:00Z',
                      settings=dict(width=1024, height=1024, quality='high', start_seconds=0, end_seconds=60))
        old = dict(state='done', settings=dict(status['settings']), elapsed_seconds=1000)
        self.assertEqual(estimate_status(status, now=NOW)['estimate_state'], 'warming_up')
        self.assertEqual(estimate_status(status, now=NOW, history=[old])['eta_seconds'], 940)
        for key, value in (('width',1536), ('quality','ultra'), ('end_seconds',30)):
            different = {**old, 'settings':{**old['settings'], key:value}}
            self.assertNotIn('eta_seconds', estimate_status(status, now=NOW, history=[different]))

    def test_export_overhead_comes_from_history_and_finished_work_has_no_zero_eta(self):
        status = dict(state='running', stage='model', progress=40, training_steps=1000)
        history = [dict(state='done', elapsed_seconds=300, quality=dict(training_steps=1000), phase_durations_seconds=dict(finalize=25))]
        log = logs([('2026-10-13T13:59:30Z',400), ('2026-10-13T13:59:40Z',600)])
        result = estimate_status(status, now=NOW, log=log, history=history)
        self.assertEqual(result['eta_seconds'], 25)
        self.assertEqual(result['eta_finalization_source'], 'history')
        self.assertEqual(estimate_status({**status,'progress':95}, now=NOW)['estimate_state'], 'finishing')

    def test_phase_durations_preserve_training_boundary(self):
        timer = PhaseTimer()
        timer.update(dict(stage='model',progress=39,step='Masking people'), now=NOW)
        result = timer.update(dict(stage='model',progress=40), now=NOW.replace(minute=1))
        self.assertEqual(result['phase'], 'training')
        self.assertEqual(result['phase_durations_seconds']['masks'], 60)
        self.assertEqual(result['phase_durations_seconds']['training'], 0)

    def test_server_markup_and_finished_model_log(self):
        status = dict(state='running', started_at=NOW.isoformat(), eta_seconds=60,
                      estimate_state='available', estimated_finish_at='2026-10-13T14:01:00Z')
        markup = app.timing_markup(status, 'Model')
        self.assertIn('เหลือประมาณ 1 นาที', markup)
        self.assertIn('21:01:00', markup)
        with tempfile.TemporaryDirectory(dir=app.ROOT/'web_data', prefix='eta_test_') as temporary:
            root = Path(temporary).resolve()
            assert root.is_relative_to(app.ROOT/'web_data')
            path = root/'job/model_runs/run/3d.log'
            path.parent.mkdir(parents=True)
            path.write_text('[2026-10-13T14:00:00Z INFO brush_cli] Done training!\n')
            with patch.object(app,'JOBS',root):
                result = app.live_training_status('job', dict(state='running',stage='model',run_id='run',progress=40,training_steps=20000))
            self.assertEqual(result['phase'], 'finalize')
            self.assertEqual(result['progress'], 95)

    def test_live_http_endpoints_include_view_and_model_finish_estimates(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory(dir=app.ROOT/'web_data', prefix='eta_http_') as temporary:
            root = Path(temporary).resolve()
            assert root.is_relative_to(app.ROOT/'web_data')
            jobs, models = root/'jobs', root/'models'
            job = jobs/'estimate'
            models.mkdir()
            job.mkdir(parents=True)
            current = datetime.now(timezone.utc)
            start = (current-timedelta(seconds=60)).isoformat()
            app.write_json(job/'views_status.json', dict(state='running',stage='views',started_at=start,
                frames=100,capture_plan=dict(total_views=400,views_per_frame=16)))
            run = '20261013_210000_1234abcd'
            server = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            with patch.multiple(app,JOBS=jobs,MODELS=models,ACTIVE_TASKS={'estimate'}), patch.object(app.Handler,'log_message'):
                thread.start()
                try:
                    base = f'http://127.0.0.1:{server.server_port}'
                    with urlopen(base+'/api/job/estimate') as response:
                        views = json.load(response)
                    self.assertEqual(views['estimate_state'],'available')
                    self.assertAlmostEqual(views['eta_seconds'],180,delta=3)
                    self.assertEqual(views['views']['estimate_state'],'available')
                    app.write_json(job/'views_status.json',dict(state='ready',stage='views'))
                    status = dict(state='running',stage='model',job='estimate',run_id=run,progress=40,training_steps=2000,started_at=start)
                    app.write_json(job/'3d_status.json',status)
                    app.write_json(job/'model_runs'/run/'status.json',status)
                    log = logs([((current-timedelta(seconds=20)).isoformat(),400),((current-timedelta(seconds=10)).isoformat(),600)])
                    (job/'model_runs'/run/'3d.log').write_text(log)
                    for endpoint in ('/api/job/estimate','/api/run/estimate/'+run):
                        with urlopen(base+endpoint) as response:
                            model = json.load(response)
                        self.assertEqual(model['estimate_state'],'available')
                        self.assertEqual(model['eta_source'],'recent_training_rate')
                        self.assertAlmostEqual(model['eta_seconds'],120,delta=3)
                    # Legacy workers have no pose total: enrich only from their
                    # own Views version, including the history endpoint.
                    app.write_json(job/'views_status.json', dict(state='ready', stage='views', run_id='v1',
                                   capture_plan={'positions':1230, 'total_views':19680}))
                    status = {**status, 'progress':29, 'phase':'poses', 'step':'COLMAP: poses', 'view_run_id':'v1'}
                    for path in (job/'3d_status.json', job/'model_runs'/run/'status.json'):
                        app.write_json(path, status)
                    (job/'model_runs'/run/'3d.log').write_text('num_reg_frames=452)\nGlobal bundle adjustment\n')
                    for endpoint in ('/api/job/estimate', '/api/run/estimate/'+run):
                        with urlopen(base+endpoint) as response:
                            pose = json.load(response)
                        self.assertEqual(pose['phase_percent'], 36.7)
                        self.assertEqual(pose['phase_total'], 1230)
                        self.assertEqual(pose['pose_activity'], 'global_bundle_adjustment')
                        self.assertIn('Global BA', pose['step'])
                    # A new Views run must not supply a denominator for the old mapper.
                    app.write_json(job/'views_status.json', dict(state='ready', run_id='v2', capture_plan={'positions':50}))
                    with urlopen(base+'/api/run/estimate/'+run) as response:
                        self.assertIsNone(json.load(response)['phase_percent'])
                    # Pose-only workers read poses.log, not a model run's log.
                    (job/'poses.log').write_text('num_reg_frames=25)\n')
                    pose = app.task_estimated_status('estimate', dict(state='running', stage='poses', progress=29,
                                                    pose_total_positions=100))
                    self.assertEqual(pose['phase_percent'], 25)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()


if __name__ == '__main__':
    unittest.main()
