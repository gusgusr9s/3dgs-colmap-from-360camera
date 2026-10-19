"""Views stores the exact COLMAP keyframes; COLMAP links them instead of decoding again."""
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runtime import compute_runtime as runtime
from app.pipeline import view_outputs
from app.web import server as app

SETTINGS = dict(width=64, height=64, fov=90, views=8, sample_every=2, projection='panorama',
                quality='preview', start_seconds=0, end_seconds=0)


def write_video(path, frames=12, size=(2560, 1280)):
    writer = app.cv2.VideoWriter(str(path), app.cv2.VideoWriter_fourcc(*'mp4v'), 10, size)
    assert writer.isOpened()
    for index in range(frames):
        image = app.np.random.default_rng(index).integers(0, 256, (size[1] // 8, size[0] // 8, 3), dtype=app.np.uint8)
        writer.write(app.cv2.resize(image, size, interpolation=app.cv2.INTER_LINEAR))
    writer.release()


class PanoramaCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'source.mp4'; write_video(cls.source)
        cls.decoded = cls.root / 'decoded'
        cls.jobs = {}
        for name, options in (('inprocess', {}), ('worker', {'isolate_cpu': True})):
            job = cls.root / name; job.mkdir()
            with patch.dict(os.environ, {'CPU_MODE': 'full', 'CPU_THREADS': '2'}):
                app.process_file(cls.source, job, view_device='cpu', **options, **SETTINGS)
            cls.jobs[name] = job
        cls.metadata = app.read_json(cls.jobs['inprocess'] / 'transforms.json')
        app.extract_spherical_keyframes(cls.source, cls.metadata, cls.decoded)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def restore(self, job, metadata=None, name='restored'):
        output = self.root / f'{job.name}-{name}'
        return view_outputs.restore_panoramas(job, self.source, metadata or self.metadata, output), output

    def test_adaptive_selection_preserves_cache_and_exposes_same_names_to_staged_mapping(self):
        import sqlite3
        from app.pipeline import adaptive_keyframes, staged_mapping
        job = self.jobs['inprocess']
        (count, note), output = self.restore(job, name='adaptive')
        before = {path.name:path.read_bytes() for path in output.glob('*.jpg')}
        selected = sorted(before)[::2]
        work = self.root/'adaptive-db';work.mkdir()
        with patch.object(adaptive_keyframes, 'select', return_value=(selected, {'test':1})):
            flags, report = adaptive_keyframes.prepare(output, work, self.metadata, {'COLMAP_KEYFRAMES':'adaptive'})
        self.assertEqual(Path(flags[1]).read_text().splitlines(), selected)
        self.assertEqual({path.name:path.read_bytes() for path in output.glob('*.jpg')}, before)
        self.assertEqual(adaptive_keyframes.recovery_names(work, set(before)),set(selected))
        with sqlite3.connect(work/'database.db') as db:
            db.execute('CREATE TABLE images(name TEXT)')
            db.executemany('INSERT INTO images VALUES (?)', [(name,) for name in selected])
        self.assertEqual(staged_mapping.database_names(work/'database.db'), selected)
        images, chunk, fresh = staged_mapping.plan_chunk(selected,set(),set())
        self.assertEqual(images,selected)
        self.assertEqual(report['selected_count'],len(chunk))

    def test_every_views_backend_caches_byte_identical_keyframes_and_links_them(self):
        expected = {path.name: path.read_bytes() for path in self.decoded.glob('*.jpg')}
        self.assertEqual(sorted(expected), [f'pano_{index:06d}.jpg' for index in range(0, 12, 2)])
        self.assertEqual(app.cv2.imread(str(self.decoded / 'pano_000000.jpg')).shape, (960, 1920, 3))
        for name, job in self.jobs.items():
            with self.subTest(backend=name):
                (count, note), output = self.restore(job)
                self.assertEqual((count, note), (6, None))
                self.assertEqual({path.name: path.read_bytes() for path in output.glob('*.jpg')}, expected)
                for path in output.glob('*.jpg'):
                    self.assertTrue(os.path.samefile(path, job / view_outputs.DIRECTORY / 'panoramas' / path.name))
                manifest = view_outputs.read_json(job / view_outputs.DIRECTORY / 'panoramas.json')
                self.assertEqual((manifest['sample_every'], manifest['size'], manifest['quality']), (2, [1920, 960], 94))
                views = view_outputs.view_manifest(job)
                self.assertEqual(len(views), 48)
                self.assertFalse(list(job.glob('.*.tmp')))

    def test_subset_of_cached_frames_is_reused(self):
        subset = {**self.metadata, 'frames': [frame for frame in self.metadata['frames'] if frame['frame_index'] >= 4]}
        (count, _), output = self.restore(self.jobs['inprocess'], subset, 'subset')
        self.assertEqual(count, 4)
        self.assertEqual(sorted(path.name for path in output.glob('*.jpg')), [f'pano_{i:06d}.jpg' for i in (4, 6, 8, 10)])

    def test_mismatches_fall_back_to_decoding(self):
        job = self.root / 'mismatch'
        import shutil
        shutil.copytree(self.jobs['inprocess'], job)
        cache = job / view_outputs.DIRECTORY
        extra = {**self.metadata, 'frames': self.metadata['frames'] + [{'frame_index': 12, 'yaw_deg': 0}]}
        self.assertIsNone(self.restore(job, extra, 'extra')[0][0])
        other = {**self.metadata, 'settings': {**self.metadata['settings'], 'sample_every': 4}}
        self.assertIsNone(self.restore(job, other, 'sample')[0][0])
        damaged = cache / 'panoramas' / 'pano_000004.jpg'
        original = damaged.read_bytes()
        damaged.write_bytes(original[:-1] + b'\0')
        self.assertIsNone(self.restore(job, name='damaged')[0][0])
        damaged.unlink()
        self.assertIsNone(self.restore(job, name='missing')[0][0])
        damaged.write_bytes(original)
        self.assertEqual(self.restore(job, name='repaired')[0][0], 6)
        manifest = view_outputs.read_json(cache / 'panoramas.json')
        view_outputs.write_json(cache / 'panoramas.json', {**manifest, 'recipe': 'old'})
        self.assertIsNone(self.restore(job, name='recipe')[0][0])
        view_outputs.write_json(cache / 'panoramas.json', {**manifest, 'source': {**manifest['source'], 'modified_ns': 1}})
        self.assertIsNone(self.restore(job, name='source')[0][0])
        # A new Views run invalidates the cache before writing anything.
        view_outputs.write_json(cache / 'panoramas.json', manifest)
        view_outputs.ViewOutputs(job, self.source, {})
        self.assertFalse((cache / 'panoramas.json').exists())
        self.assertFalse(list((cache / 'panoramas').iterdir()))
        self.assertIsNone(self.restore(job, name='reset')[0][0])

    def test_views_rewrite_never_changes_linked_files(self):
        job = self.root / 'rewrite'
        import shutil
        shutil.copytree(self.jobs['inprocess'], job)
        (count, _), linked = self.restore(job, name='before')
        before = {path.name: path.read_bytes() for path in linked.glob('*.jpg')}
        view = job / 'frame_000000_view_00.jpg'
        dataset = self.root / 'dataset_link.jpg'; os.link(view, dataset)
        saved = dataset.read_bytes()
        outputs = view_outputs.ViewOutputs(job, self.source, {})
        outputs.view(view.name, app.np.zeros((64, 64, 3), app.np.uint8))
        outputs.panorama(0, app.np.zeros((1280, 2560, 3), app.np.uint8))
        self.assertEqual(dataset.read_bytes(), saved)
        self.assertEqual({path.name: path.read_bytes() for path in linked.glob('*.jpg')}, before)

    def test_spherical_colmap_uses_cache_without_decoding_and_keeps_camera_mask(self):
        job = self.jobs['inprocess']; work = self.root / 'work'; sparse = work / 'sparse'
        def run(command, **kwargs):
            if command[1] == '-h':
                return SimpleNamespace(stdout='COLMAP 4.2.1 (without GPU support)', stderr='')
            if command[1] == 'mapper':
                (sparse / '0').mkdir(parents=True, exist_ok=True)
                (sparse / '0/images.bin').write_bytes(struct.pack('<Q', 6))
            return SimpleNamespace(returncode=0)
        quality = dict(perspective_views=48, max_same_frame_center_spread=0, sparse_points=1500,
                       path_to_scene_ratio=.2, mean_reprojection_error=1)
        log = io.StringIO()
        with patch.object(app.subprocess, 'run', side_effect=run), \
             patch.object(app, 'extract_spherical_keyframes', side_effect=AssertionError('must not decode')), \
             patch.object(app, 'build_brush_dataset', return_value=dict(quality)), \
             patch('app.pipeline.pose_person_masks.create',return_value={'images':6}), \
             patch.object(app.os, 'cpu_count', return_value=16), patch.object(runtime, 'nvidia_devices', return_value=[]):
            result = app.run_spherical_colmap(Path('colmap'), self.source, job, work, self.root / 'dataset',
                                              self.metadata, log, {'COLMAP_MAPPER_PROFILE': 'full'}, lambda *a, **k: None)
        self.assertEqual(result['input_panoramas'], 6)
        self.assertIn('reused 6 panoramas from the Views cache', log.getvalue())
        for path in (work / 'panoramas').glob('*.jpg'):
            self.assertEqual(path.read_bytes(), (self.decoded / path.name).read_bytes())
        mask = app.cv2.imread(str(work / 'camera_mask.png'), app.cv2.IMREAD_GRAYSCALE)
        self.assertEqual(mask.shape, (960, 1920))
        self.assertTrue(app.np.all(mask[:int(960 * .72)] == 255)); self.assertTrue(app.np.all(mask[int(960 * .72):] == 0))

    def test_reprocess_cleanup_removes_cache(self):
        job_root = self.root / 'jobs'; job = job_root / 'abc'
        import shutil
        shutil.copytree(self.jobs['inprocess'], job)
        with patch.object(app, 'JOBS', job_root), patch.object(app, 'job_directory', return_value=job), \
             patch.object(app, 'archive_existing_models'):
            app.cleanup_job_derivatives('abc')
        self.assertFalse((job / view_outputs.DIRECTORY).exists())


if __name__ == '__main__':
    unittest.main()
