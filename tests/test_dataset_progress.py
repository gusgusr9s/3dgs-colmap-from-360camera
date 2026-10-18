"""Real tiny spherical inputs verify progress, image compatibility and Stop."""
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pycolmap
from app.pipeline import spherical_to_brush as spherical
from app.jobs.task_control import TaskStopped, write
from scripts.observe_dataset_progress import observe
from app.jobs.process_logging import ProcessLog
from app.jobs.task_progress import progress_details
from app.pipeline import dataset_preparation
from app.web import server as web_app
from app.pipeline.training_selection import supported_colmap_points


class DatasetProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.views=self.root/'views';self.views.mkdir()
        text=self.root/'text';text.mkdir()
        (text/'cameras.txt').write_text('1 EQUIRECTANGULAR 1920 960 1920 960\n')
        (text/'images.txt').write_text(''.join(f'{i} 1 0 0 0 {i} 0 0 1 pano_{i:06d}.jpg\n960 480 1\n' for i in range(1,5)))
        (text/'points3D.txt').write_text('1 0 0 10 100 100 100 0.5 1 0 2 0 3 0 4 0\n')
        self.model=self.root/'model';self.model.mkdir()
        pycolmap.Reconstruction(text).write(self.model)
        frames=[]
        image=np.random.default_rng(17).integers(0,256,(32,32,3),dtype=np.uint8)
        for index in range(1,6):
            for yaw in (0,45):
                name=f'frame_{index:06d}_{yaw}.jpg'
                cv2.imwrite(str(self.views/name),image)
                frames.append({'file_path':name,'frame_index':index,'yaw_deg':yaw})
        metadata={'w':32,'h':32,'fl_x':16,'fl_y':16,'cx':16,'cy':16,'frames':frames}
        (self.views/'transforms.json').write_text(json.dumps(metadata))

    def test_real_progress_counts_registered_views_and_preserves_existing_pixels(self):
        reports=[];output=self.root/'dataset'
        def progress(done,total):
            self.assertFalse(output.exists(),'Incomplete datasets remain in staging')
            reports.append((done,total))
        with patch.object(spherical.time,'monotonic',side_effect=itertools.count()):
            result=spherical.build_brush_dataset(self.views,self.model,output,progress)
        self.assertEqual(reports[0],(0,8));self.assertEqual(reports[-1],(8,8))
        self.assertEqual(reports,sorted(reports))
        self.assertTrue(all(total==8 for _,total in reports))
        self.assertEqual(result['perspective_views'],8)
        self.assertEqual(result['registered_panoramas'],4)
        self.assertEqual(result['max_same_frame_center_spread'],0)
        for path in (output/'images').glob('*.jpg'):
            original=cv2.imread(str(self.views/path.name))
            expected=dataset_preparation.pad_for_brush(cv2.imencode('.jpg',original,[cv2.IMWRITE_JPEG_QUALITY,95])[1].tobytes())
            self.assertEqual(path.read_bytes(),expected)

    def test_stop_callback_interrupts_before_dataset_can_be_advertised_complete(self):
        output=self.root/'dataset'
        original=(self.views/'frame_000001_0.jpg').read_bytes()
        def stop(done,total):
            if done>=2:raise TaskStopped('stop')
        with patch.object(spherical.time,'monotonic',side_effect=itertools.count()),self.assertRaises(TaskStopped):
            spherical.build_brush_dataset(self.views,self.model,output,stop)
        self.assertFalse(output.exists())
        self.assertFalse((output/'transforms.json').exists())
        self.assertEqual((self.views/'frame_000001_0.jpg').read_bytes(),original)
        result=spherical.build_brush_dataset(self.views,self.model,output)
        self.assertGreaterEqual(result['dataset_preparation']['cache_hits'],2)

    def test_serial_and_parallel_keep_all_poses_intrinsics_and_encoded_pixels(self):
        serial=self.root/'serial';parallel=self.root/'parallel'
        with patch.dict(os.environ,{'DATASET_WORKERS':'1'}):
            spherical.build_brush_dataset(self.views,self.model,serial)
        with patch.dict(os.environ,{'DATASET_WORKERS':'4'}):
            spherical.build_brush_dataset(self.views,self.model,parallel)
        self.assertEqual((serial/'transforms.json').read_bytes(),(parallel/'transforms.json').read_bytes())
        self.assertEqual((serial/'sparse_pc.ply').read_bytes(),(parallel/'sparse_pc.ply').read_bytes())
        for image in (serial/'images').glob('*.jpg'):
            self.assertEqual(image.read_bytes(),(parallel/'images'/image.name).read_bytes())

    def test_manifest_rejects_changed_image_pose_and_missing_metadata(self):
        output=self.root/'dataset'
        spherical.build_brush_dataset(self.views,self.model,output)
        metadata=json.loads((output/'transforms.json').read_text())
        self.assertTrue(dataset_preparation.valid_manifest(self.views,output,metadata))
        image=next((output/'images').glob('*.jpg'))
        saved=image.read_bytes();image.write_bytes(saved+b'changed')
        self.assertFalse(dataset_preparation.valid_manifest(self.views,output,metadata))
        spherical.build_brush_dataset(self.views,self.model,output)
        self.assertTrue(dataset_preparation.valid_manifest(self.views,output,metadata))
        (output/'transforms.json').unlink()
        self.assertFalse(dataset_preparation.valid_manifest(self.views,output,metadata))

    def test_seed_tracks_use_recovered_sparse_root_and_matching_camera_centers(self):
        import shutil
        recovered=self.views/'model_runs/recovered/recovery/sparse'
        shutil.copytree(self.model,recovered)
        output=self.views/'brush_data'
        spherical.build_brush_dataset(self.views,recovered,output)
        selected=[1,2,3,4]
        centers={index:np.array([-index,0,0]) for index in selected}
        points=supported_colmap_points(self.views,selected,centers)
        self.assertEqual(len(points),1)
        self.assertEqual(float(points['z'][0]),10)
        self.assertIsNone(supported_colmap_points(self.views,selected,{i:np.zeros(3) for i in selected}))

    def test_progress_updates_journal_and_percentage_during_same_phase(self):
        logger=ProcessLog(self.root/'run','model','test')
        tick=logger.started_tick
        with patch('app.jobs.process_logging.time.monotonic',return_value=tick):
            logger.update(dict(state='running',progress=37,step='dataset',work_done=0,work_total=100))
        status=dict(state='running',stage='model',progress=37,step='dataset',work_done=35,work_total=100)
        with patch('app.jobs.process_logging.time.monotonic',return_value=tick+6):logger.update(status)
        self.assertIn('work_done=35',(logger.directory/'process.log').read_text(encoding='utf8'))
        self.assertEqual(progress_details(status)['phase_percent'],35)


class LiveObserverTests(unittest.TestCase):
    def test_live_mask_progress_retains_callback_count_during_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp)/'job';run=job/'model_runs/run1';run.mkdir(parents=True)
            (run/'3d.log').touch()
            status=dict(state='running',stage='model',run_id='run1',step='Masking people 17/100',
                        mask_images_written=17,mask_images_total=100)
            with patch.object(web_app,'JOBS',job.parent):
                self.assertEqual(web_app.live_training_status('job',status),status)

    def test_observer_only_appends_observations_and_exits_on_phase_change(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Path(temp);run=job/'model_runs/run1';run.mkdir(parents=True)
            images=job/'brush_data/images';images.mkdir(parents=True)
            (images/'image.jpg').write_bytes(b'unchanged input')
            status={'run_id':'run1','state':'running','phase':'dataset','started_at':'now'}
            write(run/'status.json',status);write(job/'3d_status.json',status)
            def next_phase(_):write(run/'status.json',{**status,'phase':'masks'})
            with patch('scripts.observe_dataset_progress.time.sleep',side_effect=next_phase):
                observe(job,'run1',10)
            row=json.loads((run/'process.jsonl').read_text(encoding='utf8'))
            self.assertEqual(row['event'],'observed_progress')
            self.assertEqual(row['files_present'],1)
            self.assertEqual(row['percent'],10)
            self.assertEqual((images/'image.jpg').read_bytes(),b'unchanged input')
            self.assertEqual(json.loads((job/'3d_status.json').read_text()),status)


if __name__=='__main__':unittest.main()
