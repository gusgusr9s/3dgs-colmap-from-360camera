"""Recovery must reuse the same DB IDs, preserve original files and avoid rematching."""
import hashlib
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pycolmap
from app.pipeline import colmap_recovery as recovery
from app.web import server as app


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.job=Path(self.temp.name)/'job';self.job.mkdir()
        self.work=self.job/'spherical_colmap';self.model=self.work/'sparse/0';self.model.mkdir(parents=True)
        text=self.job/'text';text.mkdir()
        (text/'cameras.txt').write_text('1 EQUIRECTANGULAR 1920 960 1920 960\n')
        (text/'images.txt').write_text(''.join(f'{i} 1 0 0 0 {i} 0 0 1 pano_{i:06d}.jpg\n960 480 1\n' for i in range(1,5)))
        (text/'points3D.txt').write_text('1 0 0 10 100 100 100 0.5 1 0 2 0 3 0 4 0\n')
        rec=pycolmap.Reconstruction(text);rec.write(self.model)
        self.metadata={'source_mode':'panorama','frames':[{'frame_index':i,'file_path':f'frame_{i}.jpg'} for i in range(1,5)]}
        (self.job/'transforms.json').write_text(json.dumps(self.metadata))
        panoramas=self.work/'panoramas';panoramas.mkdir()
        for i in range(1,5):(panoramas/f'pano_{i:06d}.jpg').write_bytes(b'fixture')
        with closing(sqlite3.connect(self.work/'database.db')) as db, db:
            db.executescript('CREATE TABLE images(image_id INTEGER,name TEXT);CREATE TABLE keypoints(image_id INTEGER,rows INTEGER);CREATE TABLE two_view_geometries(pair_id INTEGER,rows INTEGER);')
            db.executemany('INSERT INTO images VALUES (?,?)',[(i,f'pano_{i:06d}.jpg') for i in range(1,5)])
            db.executemany('INSERT INTO keypoints VALUES (?,1)',[(i,) for i in range(1,5)])
            db.execute('INSERT INTO two_view_geometries VALUES (1,20)')
        self.plan=recovery.plan(self.job,{'run_id':'old','stage':'model','view_run_id':'v1'},{'run_id':'v1'})

    def test_prepare_reads_real_model_copies_database_and_preserves_original(self):
        from app.pipeline.pose_person_masks import REPORT
        report={'recipe':'pitched_people_v1','images':4}
        (self.work/REPORT).write_text(json.dumps(report))
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.model.iterdir()}
        original_db=(self.work/'database.db').read_bytes()
        result=recovery.prepare(self.job,self.metadata,self.plan,self.job/'model_runs/new/recovery')
        self.assertEqual(result['diagnostics']['registered_positions'],4)
        self.assertEqual(result['diagnostics']['features_cached'],4)
        self.assertNotEqual(result['database'],self.work/'database.db')
        self.assertEqual(original_db,(self.work/'database.db').read_bytes())
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.model.iterdir()})
        self.assertEqual(recovery.model_count(result['model']),4)
        self.assertEqual(json.loads((result['database'].parent/REPORT).read_text()),report)
        with self.assertRaises(ValueError):recovery.prepare(self.job,self.metadata,self.plan,self.job/'model_runs/new/recovery')

    def test_adaptive_subset_survives_recovery_and_manifest_is_copied(self):
        from app.pipeline.adaptive_keyframes import MANIFEST
        self.metadata['frames'].extend([{'frame_index':5}, {'frame_index':6}])
        (self.job/'transforms.json').write_text(json.dumps(self.metadata))
        record = dict(schema=1, mode='adaptive',
                      source_names=[f'pano_{i:06d}.jpg' for i in range(1,7)],
                      selected_names=[f'pano_{i:06d}.jpg' for i in range(1,5)])
        (self.work/MANIFEST).write_text(json.dumps(record))
        plan = recovery.plan(self.job, {'run_id':'old','stage':'model','view_run_id':'v1'}, {'run_id':'v1'})
        result = recovery.prepare(self.job,self.metadata,plan,self.job/'model_runs/adaptive/recovery')
        self.assertEqual(result['diagnostics']['total_positions'],4)
        self.assertEqual(json.loads((result['database'].parent/MANIFEST).read_text()),record)
        (self.work/MANIFEST).unlink()
        with self.assertRaisesRegex(ValueError,'frame IDs'):
            recovery.prepare(self.job,self.metadata,plan,self.job/'model_runs/missing/recovery')

    def test_rejects_changed_view_run_signature_and_database_id_mapping(self):
        with self.assertRaises(ValueError):recovery.plan(self.job,{'run_id':'old','stage':'model','view_run_id':'old'},{'run_id':'new'})
        with self.assertRaises(ValueError):recovery.prepare(self.job,self.metadata,{**self.plan,'input_signature':'different'},self.job/'model_runs/a/recovery')
        with closing(sqlite3.connect(self.work/'database.db')) as db, db:db.execute('UPDATE images SET image_id=99 WHERE image_id=1')
        with self.assertRaisesRegex(ValueError,'camera names'):recovery.prepare(self.job,self.metadata,self.plan,self.job/'model_runs/b/recovery')
        self.assertFalse((self.job/'model_runs/b/recovery').exists())

    def test_recovery_of_a_recovery_uses_the_snapshot_database(self):
        first=recovery.prepare(self.job,self.metadata,self.plan,self.job/'model_runs/first/recovery')
        sparse=first['sparse']/'0';sparse.mkdir(parents=True)
        for file in recovery.MODEL_FILES:
            (sparse/file).write_bytes((first['model']/file).read_bytes())
        # The live work DB can later belong to another extraction with new IDs.
        with closing(sqlite3.connect(self.work/'database.db')) as db,db:db.execute('UPDATE images SET image_id=image_id+100')
        next_plan={**self.plan,'input_model':sparse.relative_to(self.job).as_posix()}
        second=recovery.prepare(self.job,self.metadata,next_plan,self.job/'model_runs/second/recovery')
        self.assertEqual(second['diagnostics']['registered_positions'],4)
        with closing(sqlite3.connect(second['database'])) as db:
            self.assertEqual(db.execute('SELECT min(image_id) FROM images').fetchone()[0],1)

    def test_rejects_external_model_missing_features_and_unsafe_output(self):
        for output in (self.job/'spherical_colmap',self.job.parent/'external'):
            with self.assertRaises(ValueError):recovery.prepare(self.job,self.metadata,self.plan,output)
        with self.assertRaises(ValueError):recovery.prepare(self.job,self.metadata,{**self.plan,'input_model':'../../external'},self.job/'model_runs/out/recovery')
        with closing(sqlite3.connect(self.work/'database.db')) as db, db:db.execute('DELETE FROM keypoints WHERE image_id=1')
        with self.assertRaisesRegex(ValueError,'incomplete'):recovery.prepare(self.job,self.metadata,self.plan,self.job/'model_runs/c/recovery')

    def test_resume_mapper_skips_features_and_matching_and_keeps_quality_checks(self):
        destination=self.job/'model_runs/resumed/recovery';commands=[]
        def run(command,**kwargs):
            commands.append(command)
            if command[1]=='-h':return SimpleNamespace(stdout='COLMAP 4.2.1 (without GPU support)',stderr='')
            if command[1]=='mapper':
                output=Path(command[command.index('--output_path')+1])/'0';output.mkdir()
                (output/'images.bin').write_bytes(struct.pack('<Q',4))
            return SimpleNamespace(returncode=0)
        quality={'perspective_views':64,'registered_panoramas':4,'max_same_frame_center_spread':0,
                 'sparse_points':1500,'path_to_scene_ratio':.2,'mean_reprojection_error':.5}
        # A valid checkpoint can still be too small for the production quality gate.
        prepared=recovery.prepare(self.job,self.metadata,self.plan,destination)
        prepared['diagnostics']['total_positions']=4
        with patch.object(app.subprocess,'run',side_effect=run),patch.object(recovery,'prepare',return_value=prepared), \
             patch.object(app,'build_brush_dataset',return_value=quality),patch.object(app,'extract_spherical_keyframes',side_effect=AssertionError('must not decode')):
            # Four is deliberately below the unchanged six-position quality gate.
            with self.assertRaisesRegex(RuntimeError,'at least 6'):
                app.run_spherical_colmap(Path('colmap'),self.job,self.job,self.work,self.job/'dataset',self.metadata,
                                         io.StringIO(),{'COLMAP_DEVICE':'cpu'},lambda *a,**k:None,self.plan,destination)
        self.assertEqual([c[1] for c in commands],['-h','mapper'])
        mapper=commands[-1]
        self.assertIn('--input_path',mapper)
        self.assertEqual(mapper[mapper.index('--Mapper.ba_global_frames_ratio')+1],'1.2')
        self.assertEqual(mapper[mapper.index('--Mapper.snapshot_frames_freq')+1],'50')
        self.assertTrue((self.model/'points3D.bin').is_file())

    def test_fresh_restart_archives_instead_of_deleting_saved_work(self):
        archived=recovery.archive_workspace(self.work)
        self.assertTrue((archived/'sparse/0/images.bin').is_file())
        self.assertFalse(self.work.exists())

    def test_completed_features_can_resume_before_any_camera_is_registered(self):
        for file in self.model.iterdir():file.unlink()
        recovery.record_stage(self.work,self.job,'features')
        with closing(sqlite3.connect(self.work/'database.db')) as db,db:
            db.execute('DELETE FROM two_view_geometries')
        plan=recovery.plan(self.job,{'run_id':'old','stage':'model','view_run_id':'v1'},{'run_id':'v1'})
        self.assertIsNone(plan['input_model'])
        self.assertFalse(plan['skip_matching'])
        result=recovery.prepare(self.job,self.metadata,plan,self.job/'model_runs/features/recovery')
        self.assertIsNone(result['model'])
        self.assertEqual(result['diagnostics']['features_cached'],4)
        self.assertFalse(result['skip_matching'])
        # An unverified/incomplete matcher cannot be treated as a complete cache.
        with self.assertRaisesRegex(ValueError,'incomplete'):
            recovery.prepare(self.job,self.metadata,{**plan,'skip_matching':True},self.job/'model_runs/bad/recovery')

    def test_native_resume_output_at_sparse_root_reuses_completed_mapping(self):
        root=self.work/'sparse'
        for file in self.model.iterdir():file.replace(root/file.name)
        self.model.rmdir()
        recovery.record_mapper_complete(root,self.job)
        plan=recovery.plan(self.job,{'run_id':'old','stage':'model','view_run_id':'v1'},{'run_id':'v1'})
        self.assertEqual(plan['input_model'],'spherical_colmap/sparse')
        self.assertTrue(plan['mapping_complete'])
        destination=self.job/'model_runs/complete/recovery'
        commands=[]
        def run(command,**kwargs):
            commands.append(command)
            if command[1]!='-h':raise AssertionError('Completed mapper must not run again')
            return SimpleNamespace(stdout='COLMAP 4.2.1 (without GPU support)',stderr='')
        with patch.object(app.subprocess,'run',side_effect=run):
            # Keep the six-position production quality gate even on the fast path.
            with self.assertRaisesRegex(RuntimeError,'at least 6'):
                app.run_spherical_colmap(Path('colmap'),self.job,self.job,self.work,self.job/'dataset',self.metadata,
                    io.StringIO(),{'COLMAP_DEVICE':'cpu'},lambda *a,**k:None,plan,destination)
        self.assertEqual([command[1] for command in commands],['-h'])

    def test_dead_owner_is_reported_interrupted_without_rewriting_status(self):
        status={'state':'running','stage':'model','progress':29,'step':'poses'}
        with patch.object(app,'task_is_active',return_value=False),patch.object(app,'views_status',return_value={'state':'ready'}), \
             patch.object(app,'model_status',return_value=status),patch.object(app,'read_json',return_value={}):
            result=app._combined_job_status('job')
        self.assertEqual(result['state'],'error');self.assertTrue(result['interrupted'])
        self.assertEqual(status['state'],'running')

    def test_resume_preserves_failure_evidence_and_training_options(self):
        previous={'run_id':'old','stage':'model','view_run_id':'v1','state':'error',
                  'error':'original mapper reader failure','finished_at':'2026-10-17T12:00:00+00:00',
                  'training_options':{'floor':'standard','detail':'existing','filter_blur':False}}
        app.write_json(self.job/'model_runs/old/status.json',previous)
        with patch.multiple(app,JOBS=self.job.parent,ACTIVE_TASKS=set(),TASK_TOKENS={}), \
             patch.object(app,'model_status',return_value=previous), \
             patch.object(app,'views_status',return_value={'run_id':'v1'}), \
             patch.object(app,'cached_pose_preview',return_value={}),patch.object(app.threading,'Thread'):
            try:
                self.assertTrue(app.start_3d('job',resume_colmap=True))
                history=app.read_json(self.job/'model_runs/old/status.json')
                self.assertEqual(history['error'],previous['error'])
                self.assertEqual(history['finished_at'],previous['finished_at'])
                new=app.read_json(self.job/'model_runs'/history['recovered_by']/'status.json')
                self.assertFalse(new['training_options']['filter_blur'])
            finally:app.release_task('job')


class MapperProfileTests(unittest.TestCase):
    def test_auto_large_fresh_and_resume_use_balanced(self):
        self.assertEqual(recovery.mapper_profile(59), 'full')
        self.assertEqual(recovery.mapper_profile(60), 'balanced')
        self.assertEqual(recovery.mapper_profile(1230), 'balanced')
        self.assertEqual(recovery.mapper_profile(4, resuming=True), 'balanced')

    def test_explicit_reference_and_invalid_profiles(self):
        self.assertEqual(recovery.mapper_profile(1230, {'COLMAP_MAPPER_PROFILE':'full'}, resuming=True), 'full')
        self.assertEqual(recovery.mapper_profile(4, {'COLMAP_MAPPER_PROFILE':'balanced'}), 'balanced')
        with self.assertRaisesRegex(ValueError, 'COLMAP_MAPPER_PROFILE'):
            recovery.mapper_profile(1230, {'COLMAP_MAPPER_PROFILE':'typo'})


if __name__=='__main__':unittest.main()
