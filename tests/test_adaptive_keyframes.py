import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from app.pipeline import adaptive_keyframes as keyframes


class AdaptiveKeyframesTests(unittest.TestCase):
    def test_duplicate_sequence_keeps_endpoints_and_bounded_gaps(self):
        names = [f'pano_{i:06d}.jpg' for i in range(20)]
        selected, _ = keyframes.select('.', names, loader=lambda p: p.name, compare=lambda a,b: True)
        indices = [names.index(name) for name in selected]
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 19)
        self.assertLess(len(selected), len(names))
        self.assertLessEqual(max(np.diff(indices)), 3)

    def test_change_keeps_preceding_bridge(self):
        names = [f'pano_{i:06d}.jpg' for i in range(20)]
        selected, _ = keyframes.select('.', names, loader=lambda p: p.name,
                                      compare=lambda a,b: b != names[5])
        self.assertIn(names[4], selected)
        self.assertIn(names[5], selected)

    def test_uncertain_and_short_sequences_keep_every_image(self):
        names = [f'pano_{i:06d}.jpg' for i in range(20)]
        self.assertEqual(keyframes.select('.', names, loader=lambda p: 0, compare=lambda a,b: False)[0], names)
        self.assertEqual(keyframes.select('.', names[:5], loader=lambda p: self.fail('short input decoded'))[0], names[:5])

    def test_motion_and_blank_images_are_not_discarded(self):
        rng = np.random.default_rng(12)
        image = rng.integers(0, 256, (152,512), dtype=np.uint8)
        self.assertTrue(keyframes.redundant(image, image.copy()))
        self.assertFalse(keyframes.redundant(image, np.roll(image, 5, axis=1)))
        self.assertFalse(keyframes.redundant(np.zeros_like(image), np.zeros_like(image)))

    def test_selection_manifest_round_trip_and_wrong_source_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = {'frames':[{'frame_index': i} for i in range(5)]}
            flags, report = keyframes.prepare(root, root, metadata, {'COLMAP_KEYFRAMES':'adaptive'})
            expected = set(report['source_names'])
            self.assertEqual(flags[0], '--image_list_path')
            self.assertEqual(keyframes.recovery_names(root, expected), expected)
            with self.assertRaises(ValueError):
                keyframes.recovery_names(root, expected | {'pano_999999.jpg'})
            report['selected_names'] = ['../outside.jpg']*3
            (root/keyframes.MANIFEST).write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                keyframes.recovery_names(root, expected)

    def test_disabled_mode_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(keyframes.prepare(root, root, {'frames':[]}, {}), ([], None))
            self.assertEqual(keyframes.prepare(root, root, {}, {}), ([], None))
            self.assertEqual(list(root.iterdir()), [])
            with self.assertRaisesRegex(ValueError,'Views metadata'):
                keyframes.prepare(root, root, {}, {'COLMAP_KEYFRAMES':'adaptive'})
            with self.assertRaises(ValueError):
                keyframes.prepare(root, root, {'frames':[]}, {'COLMAP_KEYFRAMES':'typo'})


if __name__ == '__main__':
    unittest.main()
