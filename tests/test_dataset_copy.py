"""Dataset preparation links proven-safe Views JPEGs; everything else keeps the re-encode."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pycolmap
from app.pipeline import dataset_preparation as prepare
from app.pipeline import spherical_to_brush as spherical
from app.pipeline import view_outputs


def noise(size, seed=0):
    return np.random.default_rng(seed).integers(0, 256, (size, size, 3), dtype=np.uint8)


class DatasetCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.views = self.root / 'views'; self.views.mkdir()
        self.output = self.root / 'out'; self.output.mkdir()

    def prepare(self, names, size=256, trusted=None):
        pairs = [(self.views / name, self.output / name) for name in names]
        return prepare.prepare_images(pairs, self.root / 'cache', size, size, trusted=trusted)

    def test_views_manifest_proves_safety_without_decoding_or_encoding(self):
        outputs = view_outputs.ViewOutputs(self.views, self.root / 'missing.mp4', {})
        for index in range(4):
            outputs.view(f'{index}.jpg', noise(256, index))
        outputs.complete()
        with patch.object(cv2, 'imdecode', side_effect=AssertionError('manifest proof must not decode')), \
             patch.object(cv2, 'imencode', side_effect=AssertionError('must not re-encode')):
            records, stats = self.prepare([f'{index}.jpg' for index in range(4)], trusted=view_outputs.view_manifest(self.views))
        self.assertEqual((stats['linked'], stats['linked_by_views_manifest'], stats['encoded']), (4, 4, 0))
        for row in records:
            self.assertTrue(os.path.samefile(self.views / row['source'], self.output / row['output']))
            self.assertEqual(row['sha256'], row['source_sha256'])

    def test_legacy_views_without_manifest_are_linked_after_a_full_decode(self):
        cv2.imwrite(str(self.views / 'a.jpg'), noise(256), [cv2.IMWRITE_JPEG_QUALITY, 90])
        with patch.object(cv2, 'imencode', side_effect=AssertionError('must not re-encode')):
            records, stats = self.prepare(['a.jpg'], trusted={'a.jpg': {'sha256': '0' * 64, 'bytes': 1}})
        self.assertEqual(records[0]['linked'], 'decoded')
        self.assertEqual((self.output / 'a.jpg').read_bytes(), (self.views / 'a.jpg').read_bytes())

    def test_unproven_files_keep_the_re_encode(self):
        image = noise(256)
        cv2.imwrite(str(self.views / 'progressive.jpg'), image, [cv2.IMWRITE_JPEG_QUALITY, 90, cv2.IMWRITE_JPEG_PROGRESSIVE, 1])
        complete = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
        (self.views / 'truncated.jpg').write_bytes(complete[:-2000])
        cv2.imwrite(str(self.views / 'wrong_size.jpg'), noise(300), [cv2.IMWRITE_JPEG_QUALITY, 90])
        names = ['progressive.jpg', 'truncated.jpg', 'wrong_size.jpg']
        self.assertGreater(min((self.views / name).stat().st_size for name in names), prepare.SAFE_BYTES)
        records, stats = self.prepare(names)
        self.assertEqual((stats['linked'], stats['encoded']), (0, 3))
        self.assertTrue((self.output / 'truncated.jpg').is_file())
        self.assertFalse(os.path.samefile(self.views / 'truncated.jpg', self.output / 'truncated.jpg'))
        # OpenCV leaves undecoded rows of a truncated JPEG uninitialised, so compare complete files only.
        for name in ('progressive.jpg', 'wrong_size.jpg'):
            decoded = cv2.imdecode(np.frombuffer((self.views / name).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
            expected = prepare.pad_for_brush(cv2.imencode('.jpg', decoded, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes())
            self.assertEqual((self.output / name).read_bytes(), expected, name)
            self.assertFalse(os.path.samefile(self.views / name, self.output / name))

    def test_small_files_are_padded_to_the_brush_minimum_with_identical_pixels(self):
        cv2.imwrite(str(self.views / 'small.jpg'), noise(32), [cv2.IMWRITE_JPEG_QUALITY, 90])
        self.assertLess((self.views / 'small.jpg').stat().st_size, prepare.BRUSH_MIN_BYTES)
        self.prepare(['small.jpg'], size=32)
        data = (self.output / 'small.jpg').read_bytes()
        self.assertEqual(len(data), prepare.SAFE_BYTES)
        unpadded = cv2.imencode('.jpg', cv2.imread(str(self.views / 'small.jpg')), [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()
        # Brush needs the frame header inside its fixed prefix, so padding goes after EOI.
        self.assertEqual(data, unpadded + bytes(prepare.SAFE_BYTES - len(unpadded)))
        np.testing.assert_array_equal(cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR),
                                      cv2.imdecode(np.frombuffer(unpadded, np.uint8), cv2.IMREAD_COLOR))

    def test_jpeg_frame_reads_baseline_header_only(self):
        data = cv2.imencode('.jpg', noise(48)[:40], [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
        self.assertEqual(prepare.jpeg_frame(data), (48, 40, 3))
        gray = cv2.imencode('.jpg', noise(48)[:, :, 0], [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
        self.assertEqual(prepare.jpeg_frame(gray), (48, 48, 1))
        scan = data.rindex(b'\xff\xda')
        marker_in_scan = data[:scan + 40] + b'\xff\xc4' + data[scan + 42:]
        for broken in (b'', data[:-1], data[:200] + data[-2:], b'\x89PNG' + data[4:], marker_in_scan):
            self.assertIsNone(prepare.jpeg_frame(broken))

    def test_dataset_build_links_views_written_by_the_views_stage(self):
        text = self.root / 'text'; text.mkdir()
        (text / 'cameras.txt').write_text('1 EQUIRECTANGULAR 1920 960 1920 960\n')
        (text / 'images.txt').write_text(''.join(f'{i} 1 0 0 0 {i} 0 0 1 pano_{i:06d}.jpg\n960 480 1\n' for i in range(1, 5)))
        (text / 'points3D.txt').write_text('1 0 0 10 100 100 100 0.5 1 0 2 0 3 0 4 0\n')
        model = self.root / 'model'; model.mkdir(); pycolmap.Reconstruction(text).write(model)
        outputs = view_outputs.ViewOutputs(self.views, self.root / 'missing.mp4', {})
        frames = []
        for index in range(1, 5):
            for yaw in (0, 45):
                name = f'frame_{index:06d}_view_{yaw // 45:02d}.jpg'
                outputs.view(name, noise(256, index * 10 + yaw))
                frames.append({'file_path': name, 'frame_index': index, 'yaw_deg': yaw})
        outputs.complete()
        (self.views / 'transforms.json').write_text(json.dumps({'w': 256, 'h': 256, 'fl_x': 128, 'fl_y': 128,
                                                                 'cx': 127.5, 'cy': 127.5, 'frames': frames}))
        dataset = self.root / 'dataset'
        result = spherical.build_brush_dataset(self.views, model, dataset)
        self.assertEqual(result['dataset_preparation']['linked_by_views_manifest'], 8)
        for frame in frames:
            self.assertTrue(os.path.samefile(self.views / frame['file_path'], dataset / 'images' / frame['file_path']))
        metadata = json.loads((dataset / 'transforms.json').read_text())
        self.assertTrue(prepare.valid_manifest(self.views, dataset, metadata))
        # Rebuilding replaces only the dataset's own names; Views keep their bytes.
        before = {frame['file_path']: (self.views / frame['file_path']).read_bytes() for frame in frames}
        spherical.build_brush_dataset(self.views, model, dataset)
        self.assertEqual({name: (self.views / name).read_bytes() for name in before}, before)


if __name__ == '__main__':
    unittest.main()
