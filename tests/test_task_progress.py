import unittest
from task_progress import progress_details


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


if __name__ == '__main__':unittest.main()
