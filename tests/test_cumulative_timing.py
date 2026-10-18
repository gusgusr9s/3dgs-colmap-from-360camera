from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from app.jobs.cumulative_timing import cumulative_timing


class CumulativeTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def save(self, group, record):
        path = self.root/group/record['run_id']/'status.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record))

    def test_resume_chain_adds_views_once_and_excludes_unrelated_runs(self):
        first = dict(run_id='a',stage='model',state='stopped',elapsed_seconds=100,
                     phase_durations_seconds={'poses':100})
        second = dict(run_id='b',stage='model',state='stopped',elapsed_seconds=50,
                      colmap_recovery={'previous_status':first},phase_durations_seconds={'poses':50})
        current = dict(run_id='c',stage='model',state='done',elapsed_seconds=200,view_run_id='v',
                       continuation={'source_run':'b'},phase_durations_seconds={'training':200})
        self.save('model_runs', second)
        self.save('model_runs', dict(run_id='unrelated',elapsed_seconds=9999))
        self.save('view_runs', dict(run_id='v',state='ready',elapsed_seconds=20))
        result = cumulative_timing(current,self.root)
        self.assertEqual(result['total_elapsed_seconds'],370)
        self.assertEqual(result['elapsed_seconds'],200)
        self.assertEqual(result['total_phase_durations_seconds'],dict(views=20,poses=150,training=200))
        self.assertEqual(result['timing_run_count'],3)
        self.assertEqual(result['timing_missing_runs'],[])
        self.assertEqual(cumulative_timing(result,self.root)['total_elapsed_seconds'],370)

    def test_running_elapsed_and_phase_advance_without_counting_pause(self):
        now = datetime(2026,10,18,12,tzinfo=timezone.utc)
        previous = dict(run_id='a',stage='model',state='stopped',elapsed_seconds=100,
                        started_at='2026-10-17T00:00:00Z',finished_at='2026-10-17T00:01:40Z')
        current = dict(run_id='b',stage='model',state='running',started_at='2026-10-18T11:59:30Z',
                       elapsed_seconds=10,phase='training',phase_durations_seconds={'training':10},
                       colmap_recovery={'previous_status':previous})
        result = cumulative_timing(current,self.root,now)
        self.assertEqual(result['total_elapsed_seconds'],130)
        self.assertEqual(result['elapsed_before_current_seconds'],100)
        self.assertEqual(result['total_phase_durations_seconds']['training'],30)

    def test_training_resume_and_refinement_follow_parent_once(self):
        first = dict(run_id='a',stage='model',state='done',elapsed_seconds=100)
        second = dict(run_id='b',stage='model',state='stopped',elapsed_seconds=50,parent_run_id='a')
        self.save('model_runs',first)
        self.save('model_runs',second)
        current = dict(run_id='c',stage='model',elapsed_seconds=20,training_recovery={'source_run':'b'},
                       continuation={'source_run':'b'})
        self.assertEqual(cumulative_timing(current,self.root)['total_elapsed_seconds'],170)

    def test_pose_only_preview_time_is_included_when_training_later(self):
        self.save('pose_runs',dict(run_id='p',stage='poses',state='ready',elapsed_seconds=70))
        current=dict(run_id='m',stage='model',elapsed_seconds=30,pose_source_run='p')
        self.assertEqual(cumulative_timing(current,self.root)['total_elapsed_seconds'],100)

    def test_cycles_missing_and_external_paths_are_bounded(self):
        self.save('model_runs',dict(run_id='b',elapsed_seconds=10,continuation={'source_run':'a'}))
        current = dict(run_id='a',stage='model',elapsed_seconds=20,continuation={'source_run':'b'})
        self.assertEqual(cumulative_timing(current,self.root)['total_elapsed_seconds'],30)
        current['continuation']['source_run']='../external'
        result = cumulative_timing(current,self.root)
        self.assertEqual(result['total_elapsed_seconds'],20)
        self.assertEqual(result['timing_missing_runs'],['../external'])

    def test_matching_historical_views_required(self):
        (self.root/'views_status.json').write_text(json.dumps(dict(run_id='new',elapsed_seconds=999)))
        result = cumulative_timing(dict(run_id='a',stage='model',view_run_id='old',elapsed_seconds=30),self.root)
        self.assertEqual(result['total_elapsed_seconds'],30)
        self.assertEqual(result['timing_missing_runs'],['Views old'])


if __name__ == '__main__':
    unittest.main()
