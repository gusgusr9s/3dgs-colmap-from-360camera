from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.pipeline import pose_person_masks as masks


class PosePersonMasksTests(unittest.TestCase):
    def setUp(self):
        self.maps = [masks.inverse_maps(360,180,yaw) for yaw in masks.YAWS]

    def probabilities(self, view):
        probability = np.zeros((len(masks.YAWS),masks.SIDE,masks.SIDE),np.float32)
        probability[view,96:288,144:240] = .95
        return probability

    def test_person_at_forward_view_is_masked_at_panorama_center(self):
        mask=masks.valid_mask(self.probabilities(0),self.maps,(720,360))
        self.assertEqual(mask.shape,(360,720))
        self.assertEqual(mask[180,360],0)
        self.assertEqual(mask[180,0],255)
        self.assertEqual(mask[0,360],255)
        self.assertLess(np.mean(mask==0),.1,'One person must not mask the whole background')

    def test_person_at_back_view_covers_both_sides_of_panorama_seam(self):
        mask=masks.valid_mask(self.probabilities(3),self.maps,(720,360))
        self.assertEqual(mask[180,0],0)
        self.assertEqual(mask[180,-1],0)
        self.assertEqual(mask[180,360],255)

    def test_no_person_retains_all_pixels(self):
        mask=masks.valid_mask(np.zeros((6,32,32),np.float32),self.maps,(720,360))
        self.assertTrue(np.all(mask==255))

    def test_creation_uses_colmap_names_preserves_images_and_records_recipe(self):
        @contextmanager
        def predictor(environment):
            self.assertEqual(environment['CPU_THREADS'],'2')
            yield lambda crops:np.zeros((len(crops),32,32),np.float32)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);panoramas=root/'panoramas';panoramas.mkdir()
            source=panoramas/'pano_000870.jpg'
            cv2.imwrite(str(source),np.zeros((180,360,3),np.uint8))
            original=source.read_bytes();progress=[]
            with patch.object(masks,'predictor',predictor):
                result=masks.create(panoramas,root/'people',{'CPU_THREADS':'2'},
                                    progress=lambda done,total:progress.append((done,total)))
            self.assertEqual(source.read_bytes(),original)
            mask=cv2.imread(str(root/'people/pano_000870.jpg.png'),cv2.IMREAD_GRAYSCALE)
            self.assertEqual(mask.shape,(180,360))
            self.assertTrue(np.all(mask==255))
            self.assertEqual(result['recipe'],masks.RECIPE)
            self.assertEqual(result['person_ignored_pixels_percent'],0)
            self.assertGreater(result['combined_ignored_pixels_percent'],27)
            self.assertEqual(progress,[(1,1)])
            self.assertEqual(json.loads((root/masks.REPORT).read_text()),result)

    def test_missing_panorama_is_an_error_before_features(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                masks.create(Path(folder),Path(folder)/'output',{})


if __name__=='__main__':unittest.main()
