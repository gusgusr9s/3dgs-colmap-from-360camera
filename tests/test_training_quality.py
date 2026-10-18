"""Publication gates, position splits, queue ownership and source-pixel contracts."""
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.web import server as app
from app.pipeline.input_review import (atomic_pixels, filter_soft_positions, floor_preview, inspect_frames,
                          recover_source_pixels, training_options)
from app.jobs.processing_queue import processing_slot
from app.pipeline.training_quality import prepare_evaluation_split, refresh_refinement_input, require_evaluation


class QualityContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='quality_contract_')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def dataset(self, positions=range(0, 181, 6), views=4, name='dataset'):
        directory = self.root / name
        (directory / 'images').mkdir(parents=True)
        frames = []
        pixels = np.random.default_rng(1).integers(0, 255, (32, 32, 3), dtype=np.uint8)
        for position in positions:
            for view in range(views):
                relative = f'images/frame_{position:06}_view_{view:02}.png'
                cv2.imwrite(str(directory / relative), pixels)
                frames.append(dict(file_path=relative, frame_index=position, yaw_deg=view*90,
                                   pitch_deg=0, transform_matrix=np.eye(4).tolist()))
        app.write_json(directory / 'transforms.json', dict(w=32,h=32,fl_x=16,fl_y=16,cx=15.5,cy=15.5,
                                                         frames=frames,ply_file_path='sparse_pc.ply'))
        (directory / 'sparse_pc.ply').write_bytes(b'initial points')
        return directory

    def test_publication_rejects_absent_corrupt_and_nonfinite_metrics(self):
        for value in ({}, {'eval_views':0}, {'eval_views':2,'eval_psnr_mean_db':99},
                      {'eval_views':3,'eval_psnr_mean_db':float('nan')},
                      {'eval_views':3,'eval_psnr_mean_db':17.9},
                      {'eval_views':3,'eval_psnr_mean_db':30,'eval_missing_views':1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_evaluation(value)
        require_evaluation({'eval_views':3,'eval_psnr_mean_db':18})

    def test_split_excludes_every_direction_of_held_out_positions_and_reuses_time_grid(self):
        reports = []
        for interval in (6, 4):
            directory = self.dataset(range(0,181,interval), name=str(interval))
            before = (directory / 'transforms.json').read_bytes()
            target, report = prepare_evaluation_split(directory, 30)
            train = app.read_json(target/'transforms.json')['frames']
            val = app.read_json(target/'transforms_val.json')['frames']
            self.assertFalse({f['frame_index'] for f in train} & {f['frame_index'] for f in val})
            self.assertEqual(report['eval_positions'], [60,120])
            self.assertEqual(len(val),8)
            self.assertTrue(all((target/f['file_path']).is_file() for f in train+val))
            self.assertEqual(before,(directory/'transforms.json').read_bytes())
            reports.append(report)
        self.assertEqual(reports[0]['signature'], reports[1]['signature'])

    def test_small_capture_requires_enough_train_and_evaluation_views(self):
        with self.assertRaises(ValueError):
            prepare_evaluation_split(self.dataset(range(3), name='too_small'))
        target, report = prepare_evaluation_split(self.dataset(range(6), views=1, name='minimum'))
        self.assertEqual(report['train_views'],3)
        self.assertEqual(report['eval_views_expected'],3)

    def test_refinement_keeps_parent_split_and_checkpoint_immutable(self):
        parent = self.dataset()
        prepare_evaluation_split(parent,30)
        original = (parent/'training_input/transforms.json').read_bytes()
        child = self.root/'refinement'
        shutil.copytree(parent,child,copy_function=os.link)
        (child/'refinement_init.ply').write_bytes(b'new checkpoint')
        target, report = refresh_refinement_input(child)
        self.assertEqual((parent/'training_input/transforms.json').read_bytes(),original)
        self.assertEqual(app.read_json(target/'transforms.json')['ply_file_path'],'refinement_init.ply')
        self.assertEqual(report,app.read_json(parent/'evaluation_split.json'))

    def test_evaluation_detects_missing_files_and_fully_masked_views(self):
        directory = self.dataset(range(6),1)
        target, report = prepare_evaluation_split(directory)
        val = app.read_json(target/'transforms_val.json')['frames']
        export = self.root/'export'
        rendered = export/'eval_10'; rendered.mkdir(parents=True)
        masks = directory/'masks'; masks.mkdir()
        for index, frame in enumerate(val):
            name = Path(frame['file_path']).name
            shutil.copy2(directory/frame['file_path'],rendered/name)
            cv2.imwrite(str(masks/name),np.full((16,16),0 if index==0 else 255,np.uint8))
        result = app.evaluate_brush_renders(directory,export,10)
        self.assertEqual(result['eval_views'],2)
        self.assertEqual(result['eval_fully_masked_views'],1)
        self.assertEqual(result['eval_psnr_mean_db'],99)
        (rendered/Path(val[-1]['file_path']).name).unlink()
        result = app.evaluate_brush_renders(directory,export,10)
        self.assertEqual(result['eval_missing_views'],1)
        with self.assertRaises(ValueError):require_evaluation(result)

    def test_blur_filter_preserves_endpoints_test_anchors_and_temporal_overlap(self):
        meta = {'frames':[dict(frame_index=i,file_path=str(i)) for i in range(0,81,4)]}
        report = {'samples':[dict(frame_index=i,soft=i in (0,4,8,60,80),sharpness=i) for i in range(0,81,4)]}
        removed = filter_soft_positions(meta,report,30)
        self.assertEqual(removed,[4])
        kept = [f['frame_index'] for f in meta['frames']]
        self.assertTrue({0,8,60,80}.issubset(kept))
        self.assertLessEqual(max(b-a for a,b in zip(kept,kept[1:])),9)

    def test_atomic_image_edit_does_not_modify_parent_hardlink(self):
        parent = self.root/'parent.png'; child = self.root/'child.png'
        cv2.imwrite(str(parent),np.zeros((8,8,3),np.uint8)); os.link(parent,child)
        original = parent.read_bytes()
        atomic_pixels(child,np.full((8,8,3),200,np.uint8))
        self.assertEqual(parent.read_bytes(),original)
        self.assertNotEqual(child.read_bytes(),original)

    def test_lossless_mode_uses_exact_source_frame_and_updates_intrinsics(self):
        directory = self.dataset([12],1)
        meta = app.read_json(directory/'transforms.json')
        panorama = np.random.default_rng(4).integers(0,255,(32,64,3),dtype=np.uint8)
        original = (directory/meta['frames'][0]['file_path']).read_bytes()
        with patch('app.pipeline.input_review.frames_from_input',return_value=iter([(12,panorama)])) as decode:
            result = recover_source_pixels(Path('source.mp4'),directory,meta)
        self.assertEqual(decode.call_args.args[2:],(12,13))
        self.assertEqual(result['width'],2048)
        self.assertEqual(meta['fl_x'],1024)
        self.assertEqual(meta['cx'],1023.5)
        self.assertEqual(meta['frames'][0]['frame_index'],12)
        self.assertEqual(cv2.imread(str(directory/meta['frames'][0]['file_path'])).shape,(2048,2048,3))

    def test_floor_preview_retains_more_ground_without_changing_image(self):
        image=np.full((64,64,3),100,np.uint8)
        meta={'w':64,'fl_x':32}
        amounts=[]
        for mode in ('standard','more_floor','person_only'):
            result=floor_preview(image,{'pitch_deg':55},meta,mode)
            amounts.append(np.count_nonzero(np.any(result!=image,axis=2)))
        self.assertGreater(amounts[0],amounts[1]);self.assertEqual(amounts[2],0)
        self.assertTrue(np.all(image==100))
        with self.assertRaises(ValueError):training_options({'floor':'unknown'})


class QueueContracts(unittest.TestCase):
    def test_os_lock_excludes_another_process_even_with_earlier_ticket(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = """
import sys
from pathlib import Path
from app.web import server as app
from app.jobs import processing_queue as q
q.time.time_ns=lambda:1
root=Path(sys.argv[1])
with q.processing_slot(root,app.task_lock_alive,lambda _: (root/'waiting').touch(),.01):
    (root/'entered').touch()
"""
            process = None
            try:
                with processing_slot(root,app.task_lock_alive):
                    process = subprocess.Popen([sys.executable,'-c',script,folder],cwd=app.ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    deadline=time.monotonic()+15
                    while not (root/'waiting').exists() and process.poll() is None and time.monotonic()<deadline:
                        time.sleep(.03)
                    self.assertTrue((root/'waiting').exists())
                    self.assertFalse((root/'entered').exists())
                stdout,stderr=process.communicate(timeout=15)
                self.assertEqual(process.returncode,0,stderr.decode(errors='replace'))
                self.assertTrue((root/'entered').exists())
            finally:
                if process is not None and process.poll() is None:
                    process.terminate();process.communicate(timeout=5)

    def test_fifo_reentrancy_and_exception_release(self):
        with tempfile.TemporaryDirectory() as folder:
            started=threading.Event();release=threading.Event();waiting=threading.Event();order=[];errors=[]
            def first():
                try:
                    with processing_slot(folder, app.task_lock_alive):
                        with processing_slot(folder, app.task_lock_alive):
                            order.append('first');started.set();release.wait(5)
                        raise RuntimeError('simulated failure')
                except RuntimeError:errors.append('released')
            def second():
                with processing_slot(folder, app.task_lock_alive, lambda ahead: waiting.set(), .01):
                    order.append('second')
            a=threading.Thread(target=first);a.start()
            self.assertTrue(started.wait(3))
            b=threading.Thread(target=second);b.start()
            try:
                self.assertTrue(waiting.wait(3));self.assertEqual(order,['first'])
            finally:
                release.set();a.join(5);b.join(5)
            self.assertFalse(a.is_alive() or b.is_alive())
            self.assertEqual(order,['first','second']);self.assertEqual(errors,['released'])
            self.assertEqual(list(Path(folder).glob('*.json')),[])

    def test_dead_owner_does_not_block_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            stale=Path(folder)/'00000000000000000000_dead.json';stale.write_text('{"pid":99999999}')
            with processing_slot(folder,app.task_lock_alive):self.assertFalse(stale.exists())


if __name__ == '__main__':
    unittest.main()
