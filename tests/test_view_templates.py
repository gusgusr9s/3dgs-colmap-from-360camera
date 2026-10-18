"""Recipe proof and source adaptation must not transplant unsafe sampling."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.view_templates import model_template, adapt_template


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.profile = dict(width=6144,height=3072,fps=2,frame_count=246,kind='video',
                            duration_seconds=123,detected_projection='panorama',aspect_ratio=2,
                            fingerprint='6k',modified_ns=1)
        self.record = dict(state='done',model='proven.ply',job='concourse',source_profile=self.profile,
                           view_settings=dict(width=1536,height=1536,fov=90,views=8,sample_every=1,
                                              quality='ultra',projection='panorama',start_seconds=0,end_seconds=0),
                           quality=dict(registered_panoramas=246,input_panoramas=246,
                                        eval_psnr_mean_db=23,training_steps=30000,max_splats=4000000))
    def test_native_source_retains_proven_recipe(self):
        result = adapt_template(model_template(self.record), self.profile)
        self.assertEqual(result['adapted_settings'], self.record['view_settings'])
        self.assertEqual(result['capture_plan']['total_views'], 3936)
    def test_30fps_2k_does_not_copy_2fps_interval(self):
        profile = {**self.profile,'width':1920,'height':960,'fps':30,'frame_count':900,
                   'duration_seconds':30,'fingerprint':'other','modified_ns':2}
        result = adapt_template(model_template(self.record), profile)
        self.assertEqual(result['adapted_settings']['width'], 448)
        self.assertEqual(result['capture_plan']['effective_sample_every'], 4)
        self.assertEqual(result['adapted_settings']['views'], 8)
        self.assertEqual(result['capture_plan']['views_per_frame'], 16)
        self.assertIn('depth', result['warning'])
    def test_old_trim_is_not_copied_to_another_video(self):
        self.record['view_settings'].update(start_seconds=30,end_seconds=50)
        result = adapt_template(model_template(self.record), {**self.profile,'fingerprint':'new'})
        self.assertEqual(result['adapted_settings']['start_seconds'],0)
        self.assertEqual(result['adapted_settings']['end_seconds'],0)
    def test_failed_or_weak_models_are_not_success_templates(self):
        for key,value in [('state','error'),('selected_frames',[1,2])]:
            with self.subTest(key=key):
                record=deepcopy(self.record);record[key]=value
                self.assertIsNone(model_template(record))
        for key,value in [('eval_psnr_mean_db',17),('eval_psnr_mean_db',float('nan')),('registered_panoramas',2)]:
            record=deepcopy(self.record);record['quality'][key]=value
            self.assertIsNone(model_template(record))
        record=deepcopy(self.record);record['quality'].update(training_steps=10000,cumulative_training_steps=40000)
        self.assertIsNone(model_template(record),'Refinement steps alone do not reproduce a base training recipe')
    def test_wrong_projection_is_not_offered(self):
        self.assertIsNone(adapt_template(model_template(self.record),{**self.profile,'detected_projection':'walkthrough'}))

    def test_persisted_recipe_survives_without_large_model_file(self):
        from app.web import server as w
        original=w.read_json
        def read(path,default=None):
            if path.name=='view_template_presets.json':return {'templates':[self.record]}
            return original(path,default)
        with tempfile.TemporaryDirectory() as folder, patch.object(w,'MODELS',Path(folder)), patch.object(w,'read_json',side_effect=read):
            templates=w.suggested_templates(self.profile)
        self.assertEqual(len(templates),1)
        self.assertEqual(templates[0]['adapted_settings']['width'],1536)
        self.assertFalse(templates[0]['model_available'])


if __name__ == '__main__': unittest.main()
