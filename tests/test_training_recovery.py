"""Weight checkpoint validation and recovery must preserve the original split."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from app.pipeline import training_recovery as recovery
from app.jobs import task_control


def ply(path, value=0, count=2000):
    names=['x','y','z','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3','f_dc_0','f_dc_1','f_dc_2']
    header='ply\nformat binary_little_endian 1.0\nelement vertex '+str(count)+'\n'
    header+=''.join('property float '+name+'\n' for name in names)+'end_header\n'
    values=np.full((count,len(names)),value,dtype='<f4');values[:,7]=1
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(header.encode()+values.tobytes())


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.job=Path(self.temp.name)/'job'
        self.run=self.job/'model_runs/old'
        dataset=self.run/'dataset/training_input';dataset.mkdir(parents=True)
        for name,frames in [('transforms.json',[1,2,3]),('transforms_val.json',[4])]:
            (dataset/name).write_text(json.dumps({'frames':frames,'ply_file_path':'seed.ply'}))
        (dataset/'pixels.png').write_bytes(b'pixels')
        command=['brush',str(dataset),'--total-steps','20000','--export-path',str(self.run/'brush_export'),
                 '--export-every','5000','--export-name','splat_{iter}.ply']
        recovery.record(self.run,command,{'evaluation_split':{'signature':'held-out'}})

    def test_truncated_nonfinite_checkpoint_is_skipped_and_valid_files_retained(self):
        export=self.run/'brush_export'
        for step in (5000,10000,15000):ply(export/f'splat_{step:05}.ply')
        ply(export/'splat_20000.ply',float('nan'))
        (export/'splat_25000.ply').write_bytes(b'ply\n')
        self.assertEqual([step for step,_ in recovery.checkpoints(export)],[15000,10000,5000])
        recovery.prune(export)
        self.assertEqual([step for step,_ in recovery.checkpoints(export)],[15000,10000])
        self.assertTrue((export/'splat_10000.ply').exists())
        self.assertFalse(recovery.checkpoint_valid(export/'splat_20000.ply'))

    def test_recovery_reuses_step_and_split_without_mutating_original(self):
        ply(self.run/'brush_export/splat_05000.ply')
        previous={'run_id':'old','view_run_id':'v1'}
        plan=recovery.plan(self.job,previous,{'run_id':'v1'})
        target=self.job/'model_runs/new'
        before=(self.run/'dataset/training_input/transforms.json').read_bytes()
        command,quality,_=recovery.prepare(self.job,plan,target)
        self.assertEqual(command[command.index('--start-iter')+1],'5000')
        self.assertEqual(command[command.index('--total-steps')+1],'20000')
        self.assertEqual(quality['evaluation_split']['signature'],'held-out')
        self.assertEqual(task_control.read(target/'dataset/training_input/transforms_val.json')['frames'],[4])
        self.assertEqual(task_control.read(target/'dataset/training_input/transforms.json')['ply_file_path'],'resume_init.ply')
        self.assertEqual((self.run/'dataset/training_input/transforms.json').read_bytes(),before)
        self.assertTrue(recovery.checkpoint_valid(target/'dataset/training_input/resume_init.ply'))

    def test_final_export_is_complete_and_keeps_checkpoint_available(self):
        export=self.run/'brush_export'
        checkpoint=export/'splat_20000.ply'
        ply(checkpoint)
        before=checkpoint.read_bytes()
        alias=recovery.final_export(export,20000)
        self.assertEqual(alias.read_bytes(),before)
        self.assertEqual(checkpoint.read_bytes(),before)
        self.assertEqual(list(export.glob('.final-*.tmp')),[])

    def test_changed_input_and_view_run_or_external_checkpoint_are_rejected(self):
        ply(self.run/'brush_export/splat_05000.ply')
        previous={'run_id':'old','view_run_id':'v1'}
        with self.assertRaises(ValueError):recovery.plan(self.job,previous,{'run_id':'v2'})
        plan=recovery.plan(self.job,previous,{'run_id':'v1'})
        with self.assertRaises(ValueError):recovery.prepare(self.job,{**plan,'checkpoint':'../../outside.ply'},self.job/'model_runs/new')
        (self.run/'dataset/training_input/pixels.png').write_bytes(b'changed')
        with self.assertRaises(ValueError):recovery.prepare(self.job,plan,self.job/'model_runs/new')
        with self.assertRaises(ValueError):recovery.plan(self.job,previous,{'run_id':'v1'})


class TrainingPipelineTests(unittest.TestCase):
    def setUp(self):
        import test_task_history
        self.fixture = test_task_history.HistoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_resume_skips_colmap_masks_and_reuses_evaluation_split(self):
        from app.web import server as app
        fixture = self.fixture
        previous = fixture.train()
        parent = fixture.directory/'model_runs'/previous['run_id']
        ply(parent/'brush_export/splat_05000.ply')
        plan = recovery.plan(fixture.directory,previous,app.views_status(fixture.job))
        identifier = app.allocate_model_run(fixture.job)
        run = fixture.directory/'model_runs'/identifier
        app.write_json(run/'status.json',{**app.read_json(run/'status.json'),'training_recovery':plan})
        commands=[]
        def train(command, **kwargs):
            commands.append(command)
            return fixture.fake_train(command, **kwargs)
        with patch.object(app.subprocess,'run',side_effect=train), \
             patch.object(app,'run_spherical_colmap',side_effect=AssertionError('must reuse poses')), \
             patch.object(app,'create_dynamic_person_masks',side_effect=AssertionError('must reuse masks')):
            app.resume_training(fixture.job,identifier)
        result=app.read_json(run/'status.json')
        self.assertEqual(result['state'],'done',result.get('error'))
        self.assertEqual(len(commands),1)
        self.assertEqual(commands[0][commands[0].index('--start-iter')+1],'5000')
        self.assertEqual(result['quality']['evaluation_split']['signature'],previous['quality']['evaluation_split']['signature'])
        self.assertGreaterEqual(result['quality']['eval_views'],3)
        self.assertTrue((app.MODELS/result['model']).is_file())

    def test_resume_cannot_publish_without_valid_evaluation(self):
        from app.web import server as app
        fixture=self.fixture
        previous=fixture.train()
        parent=fixture.directory/'model_runs'/previous['run_id']
        ply(parent/'brush_export/splat_20000.ply')
        plan=recovery.plan(fixture.directory,previous,app.views_status(fixture.job))
        identifier=app.allocate_model_run(fixture.job)
        run=fixture.directory/'model_runs'/identifier
        app.write_json(run/'status.json',{**app.read_json(run/'status.json'),'training_recovery':plan})
        before=set(app.MODELS.glob('*.ply'))
        with patch.object(app,'evaluate_brush_renders',return_value={'eval_views':0}):
            app.resume_training(fixture.job,identifier)
        self.assertEqual(app.read_json(run/'status.json')['state'],'error')
        self.assertEqual(set(app.MODELS.glob('*.ply')),before)


if __name__=='__main__':unittest.main()
