"""Continue chooses completed stages without rebuilding immutable inputs."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pipeline_resume
import dataset_preparation
import web_app as app


class ResumeTests(unittest.TestCase):
    def setUp(self):
        import test_task_history
        self.fixture=test_task_history.HistoryTests();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        previous=self.fixture.train()
        self.previous={**previous,'state':'stopped'}
        self.run=self.fixture.directory/'model_runs'/previous['run_id']
        app.write_json(self.run/'status.json',self.previous)
        app.write_json(self.fixture.directory/'3d_status.json',self.previous)

    def plan(self):
        return pipeline_resume.plan(self.fixture.directory,self.previous,app.views_status(self.fixture.job),self.fixture.preview)

    def test_prepared_training_dataset_reused_before_any_checkpoint(self):
        selected=self.plan()
        self.assertEqual(selected['kind'],'training')
        self.assertIsNone(selected['recovery']['checkpoint'])
        self.assertEqual(selected['recovery']['step'],0)

    def test_continue_before_first_checkpoint_reuses_prepared_split_and_initialization(self):
        dataset=self.run/'dataset'
        original_split=(dataset/'training_input/transforms_val.json').read_bytes()
        original_init=(dataset/'training_input/transforms.json').read_bytes()
        with patch.object(app.threading,'Thread'):
            self.assertTrue(app.start_continue(self.fixture.job))
        new_path=next(path for path in (self.fixture.directory/'model_runs').glob('*/status.json') if path.parent!=self.run)
        new=app.read_json(new_path)
        with patch.object(app,'run_spherical_colmap',side_effect=AssertionError('must not repeat COLMAP')), \
             patch.object(app,'build_brush_dataset',side_effect=AssertionError('must not repeat image preparation')), \
             patch.object(app.subprocess,'run',side_effect=self.fixture.fake_train):
            app.resume_training(self.fixture.job,new['run_id'])
        final=app.model_status(self.fixture.job)
        self.assertEqual(final['state'],'done',final.get('error'))
        self.assertIsNone(final['training_recovery']['checkpoint'])
        self.assertEqual((new_path.parent/'dataset/training_input/transforms_val.json').read_bytes(),original_split)
        self.assertEqual((new_path.parent/'dataset/training_input/transforms.json').read_bytes(),original_init)

    def test_latest_valid_checkpoint_beats_prepared_dataset(self):
        from test_training_recovery import ply
        ply(self.run/'brush_export/splat_05000.ply')
        (self.run/'brush_export/splat_10000.ply').write_bytes(b'partial')
        selected=self.plan()
        self.assertEqual(selected['kind'],'training')
        self.assertEqual(selected['recovery']['step'],5000)

    def test_pose_resume_never_runs_colmap_or_reencodes_images(self):
        fixture=self.fixture
        (self.run/'training_command.json').unlink()
        self.assertEqual(self.plan()['kind'],'poses')
        original={path.name:path.read_bytes() for path in (fixture.directory/'brush_data').glob('frame_*.jpg')}
        with patch.object(app.threading,'Thread'):
            self.assertTrue(app.start_continue(fixture.job))
        new_path=next(path for path in (fixture.directory/'model_runs').glob('*/status.json') if path.parent!=self.run)
        new=app.read_json(new_path)
        self.assertEqual(new['continuation']['source_run'],self.previous['run_id'])
        with patch.object(app,'run_spherical_colmap',side_effect=AssertionError('must reuse poses')), \
             patch.object(app,'build_brush_dataset',side_effect=AssertionError('must reuse normalized images')), \
             patch.object(app.subprocess,'run',side_effect=fixture.fake_train):
            app.generate_3d(fixture.job,identifier=new['run_id'])
        self.assertEqual(app.model_status(fixture.job)['state'],'done',app.model_status(fixture.job).get('error'))
        self.assertEqual(original,{p.name:p.read_bytes() for p in (fixture.directory/'brush_data').glob('frame_*.jpg')})

    def test_changed_camera_pixels_and_views_do_not_reuse_ready_stage(self):
        (self.run/'training_command.json').unlink()
        self.assertTrue(pipeline_resume.ready_pose_dataset(self.fixture.directory,self.fixture.preview))
        dataset=self.fixture.directory/'brush_data/transforms.json'
        metadata=app.read_json(dataset);metadata['frames'][0]['transform_matrix'][0][3]+=1
        app.write_json(dataset,metadata)
        self.assertFalse(pipeline_resume.ready_pose_dataset(self.fixture.directory,self.fixture.preview))
        with self.assertRaises(ValueError):
            pipeline_resume.plan(self.fixture.directory,self.previous,{'run_id':'changed'},self.fixture.preview)

    def test_one_continue_button_replaces_manual_stage_choices(self):
        page=app.job_detail_page(self.fixture.job)
        self.assertEqual(page.count('name="action" value="resume_task"'),1)
        self.assertNotIn('value="resume_colmap"',page)
        self.assertNotIn('value="resume_training"',page)


if __name__=='__main__':unittest.main()
