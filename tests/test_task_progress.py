import unittest
from pathlib import Path
import tempfile
from app.jobs.colmap_progress import pose_log_details
from app.jobs.task_progress import progress_details


class ProgressTests(unittest.TestCase):
    def test_views_use_real_counts(self):
        status=progress_details(dict(state='running',stage='views',frames=25,capture_plan={'total_views':100}))
        self.assertEqual(status['phase_percent'],25)
        self.assertIn('25/100',status['progress_label'])

    def test_features_and_matching_parse_native_logs(self):
        for progress,line,expected in [(12,'Processed file [30/120]',25),(21,'Processing image [7/20]',35)]:
            status=progress_details(dict(state='running',stage='model',progress=progress),line)
            self.assertEqual(status['phase_percent'],expected)
            self.assertIn('โดยประมาณ',status['progress_label'])

    def test_masks_and_train_are_independent_of_weighted_overall(self):
        status=progress_details(dict(state='running',stage='model',progress=39,step='Masking people',
                                     mask_images_written=24,mask_images_total=48))
        self.assertEqual(status['phase_percent'],50)
        status=progress_details(dict(state='running',stage='model',progress=67,training_steps=20000),
                                'Refine iter 10000, 9000 splats')
        self.assertEqual(status['phase_percent'],50)

    def test_pose_and_export_are_indeterminate_and_queue_has_no_fake_percent(self):
        for progress in (29,95):
            status=progress_details(dict(state='running',stage='model',progress=progress))
            self.assertIsNone(status['phase_percent'])
            self.assertIn('ยังวัด %',status['progress_label'])
        self.assertIn('ยังไม่เริ่ม',progress_details({'state':'queued'})['progress_label'])
        self.assertEqual(progress_details({'state':'done'})['phase_percent'],100)

    def test_pose_coverage_and_ba_are_separate_from_overall_progress(self):
        source = dict(state='running', stage='model', progress=29, pose_total_positions=1230)
        log = 'Registering image #269 (num_reg_frames=452)\nRetriangulation and Global bundle adjustment\n'
        status = progress_details(source, log)
        self.assertEqual(status['phase_percent'], 36.7)
        self.assertEqual(status['phase_done'], 452)
        self.assertEqual(status['phase_total'], 1230)
        self.assertEqual(status['pose_activity'], 'global_bundle_adjustment')
        self.assertTrue(status['phase_percent_is_estimate'])
        self.assertIn('Global BA', status['step'])
        self.assertIn('452/1,230', status['progress_label'])
        self.assertEqual(status['progress'], 29)
        self.assertNotIn('phase_done', source)

    def test_all_cameras_does_not_claim_mapper_is_finished(self):
        status = progress_details(dict(state='running', stage='model', progress=29,
                                       pose_total_positions=60), 'num_reg_frames=60)\nGlobal bundle adjustment')
        self.assertEqual(status['pose_coverage_percent'], 100)
        self.assertEqual(status['phase_percent'], 99)
        self.assertIn('BA ตอนท้าย', status['progress_label'])

    def test_pose_retry_uses_latest_reconstruction_not_largest_count(self):
        source = dict(state='running', stage='model', progress=29, pose_total_positions=100)
        for log, count in [('num_reg_frames=80)\nnum_reg_frames=3)', 3),
                           ('num_reg_frames=80)\n$ colmap mapper --database_path fresh.db\n', 0),
                           ('num_reg_frames=80)\n$ colmap mapper --input_path snapshot\n', 40)]:
            status = progress_details({**source, 'pose_initial_registered_positions':40}, log)
            self.assertEqual(status['phase_done'], count)

    def test_noisy_ba_keeps_last_count_and_replaced_log_resets_it(self):
        source = dict(state='running', stage='poses', progress=29, pose_total_positions=100)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'poses.log'
            path.write_text('num_reg_frames=40)\nGlobal bundle adjustment\n')
            self.assertEqual(pose_log_details(path, source)['registered_positions_so_far'], 40)
            with path.open('a') as handle:
                handle.write('Linear solver failure\n' * 20000)
            latest = pose_log_details(path, source)
            self.assertEqual(latest['registered_positions_so_far'], 40)
            self.assertEqual(latest['pose_activity'], 'global_bundle_adjustment')
            path.write_text('$ colmap mapper --database_path fresh.db\n')
            self.assertEqual(pose_log_details(path, source)['registered_positions_so_far'], 0)

    def test_partial_registration_number_does_not_drop_progress(self):
        source = dict(state='running', stage='model', progress=29, pose_total_positions=1230,
                      pose_initial_registered_positions=545)
        log = 'Registering image #189 (num_reg_frames=558)\nRegistering image #188 (num_reg_frames=55'
        self.assertEqual(progress_details(source, log)['phase_done'], 558)
        self.assertEqual(progress_details(source, log+'9)\n')['phase_done'], 559)
        self.assertEqual(progress_details(source, 'Registering image #188 (num_reg_frames=55')['phase_done'], 545)

    def test_stopped_pose_preserves_reason_and_recovery_initial_count(self):
        source = dict(state='stopped', stage='model', progress=29, step='หยุดตามคำขอ',
                      pose_total_positions=100, pose_initial_registered_positions=40)
        self.assertEqual(progress_details(source)['step'], source['step'])
        self.assertEqual(progress_details({**source, 'state':'running'})['phase_done'], 40)


if __name__ == '__main__':unittest.main()
