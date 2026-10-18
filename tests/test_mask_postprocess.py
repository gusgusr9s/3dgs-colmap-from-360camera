"""CUDA mask post-processing must reproduce the OpenCV path exactly and fall back to it."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import torch

from app.runtime import mask_postprocess as post
from app.runtime.mask_compute import MaskPredictor

DEVICES = [torch.device('cpu')] + ([torch.device('cuda')] if torch.cuda.is_available() else [])


def blobs(count, size, seed):
    """Smooth person-like probability maps with values crossing the 0.05 threshold."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    maps = np.zeros((count, size, size), np.float32)
    for index in range(count):
        for _ in range(4):
            cx, cy, radius = rng.uniform(-10, size + 10), rng.uniform(-10, size + 10), rng.uniform(4, size / 4)
            maps[index] = np.maximum(maps[index], np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * radius ** 2)))
    return maps


def nadir(width, height, rows):
    value = np.zeros((height, width), np.uint8); value[height - rows:] = 1
    return value


class MorphologyBorderTests(unittest.TestCase):
    def test_box_morphology_matches_opencv_default_borders(self):
        rng = np.random.default_rng(3)
        masks = (rng.random((6, 64, 80)) > .93).astype(np.uint8)
        masks[0] = 1; masks[1] = 0; masks[1, 0, 0] = masks[1, -1, -1] = 1  # full image, single corner pixels
        masks[2, :, :3] = 1; masks[3, -2:, :] = 1                          # bands on the image edges
        for device in DEVICES:
            processor = post.CudaPostprocess(torch, device)
            tensor = torch.from_numpy(masks).to(device)
            for kernel in (post.CLOSE_KERNEL, post.DILATE_KERNEL, 3):
                box = np.ones((kernel, kernel), np.uint8)
                dilated = processor._dilate(tensor, kernel).cpu().numpy()
                eroded = processor._erode(tensor, kernel).cpu().numpy()
                closed = processor._erode(processor._dilate(tensor, kernel), kernel).cpu().numpy()
                for index, mask in enumerate(masks):
                    np.testing.assert_array_equal(dilated[index], cv2.dilate(mask, box), f'{device} dilate {kernel}')
                    np.testing.assert_array_equal(eroded[index], cv2.erode(mask, box), f'{device} erode {kernel}')
                    np.testing.assert_array_equal(closed[index], cv2.morphologyEx(mask, cv2.MORPH_CLOSE, box))
            # The image edge never erodes, and a corner pixel grows only inward.
            self.assertTrue((closed[0] == 1).all())
            self.assertEqual(int(dilated[1].sum()), 2 * 2 * 2)  # 3x3 kernel: two 2x2 corner blocks


class UpsamplingParityTests(unittest.TestCase):
    def test_ignored_masks_match_opencv_on_synthetic_maps(self):
        maps = blobs(6, 130, seed=7)
        for device in DEVICES:
            processor = post.CudaPostprocess(torch, device)
            for size in ((390, 390), (520, 390), (130, 130)):
                nadirs = [nadir(*size, rows) for rows in (0, 20, 20, 60, 0, 5)]
                result = processor.batch(torch.from_numpy(maps).to(device), [size] * len(maps), nadirs)
                for probability, mask, floor in zip(maps, result, nadirs):
                    np.testing.assert_array_equal(mask, post.cpu_ignored(probability, size, floor), f'{device} {size}')

    def test_upsampled_values_follow_opencv_linear_weights(self):
        maps = blobs(2, 130, seed=11)
        processor = post.CudaPostprocess(torch, DEVICES[-1])
        tensor = torch.from_numpy(maps).to(DEVICES[-1])
        x0, x1, weight = processor._axis(130, 390)
        rows = processor._lerp(tensor[..., x0], tensor[..., x1], weight)
        upsampled = processor._lerp(rows[..., x0, :], rows[..., x1, :], weight[:, None]).cpu().numpy()
        reference = np.stack([cv2.resize(value, (390, 390), interpolation=cv2.INTER_LINEAR) for value in maps])
        self.assertLess(float(np.abs(upsampled - reference).max()), 1e-6)

    def test_near_threshold_pixels_use_the_fused_form(self):
        # Values packed around 0.05 make float32 and fused lerp disagree; the refinement must follow the fused form.
        rng = np.random.default_rng(13)
        maps = (post.THRESHOLD + rng.uniform(-3e-7, 3e-7, (3, 40, 40))).astype(np.float32)
        for device in DEVICES:
            processor = post.CudaPostprocess(torch, device)
            tensor = torch.from_numpy(maps).to(device)
            x0, x1, x_weight = processor._axis(40, 130); y0, y1, y_weight = processor._axis(40, 130)
            rows = processor._lerp(tensor[..., x0], tensor[..., x1], x_weight)
            fused = processor._lerp(rows[..., y0, :], rows[..., y1, :], y_weight[:, None]) > post.THRESHOLD
            plain = (rows[..., y0, :] + y_weight[:, None] * (rows[..., y1, :] - rows[..., y0, :])) > post.THRESHOLD
            self.assertGreater(int((plain != fused).sum()), 0)  # the case is actually exercised
            self.assertTrue(torch.equal(processor.above_threshold(tensor, (130, 130)), fused))

    def test_downscaling_is_left_to_opencv(self):
        processor = post.CudaPostprocess(torch, torch.device('cpu'))
        result = processor.batch(torch.zeros(2, 64, 64), [(32, 32), (64, 64)], [nadir(32, 32, 0), nadir(64, 64, 0)])
        self.assertIsNone(result[0]); self.assertEqual(result[1].shape, (64, 64))

    def test_valid_mask_equals_legacy_numpy_conversion(self):
        ignored = (np.random.default_rng(5).random((77, 91)) > .6).astype(np.uint8)
        valid, fraction = post.valid_mask(ignored)
        np.testing.assert_array_equal(valid, np.where(ignored > 0, 0, 255).astype(np.uint8))
        self.assertEqual(fraction, float(np.mean(ignored)))


class FakeCudaPredictor(MaskPredictor):
    """Reports CUDA but runs on the CPU, so the CUDA branch is exercised on every host."""
    def __init__(self, model, requested=None):
        super().__init__(model, 'cpu'); self.backend = 'CUDA'

    def probabilities(self, inputs, keep_device=False):
        with torch.inference_mode():
            person = self.active_model(inputs)['out'].softmax(1)[:, 15]
        return person if keep_device else person.numpy()


class PipelineFallbackTests(unittest.TestCase):
    def run_masks(self, root, predictor=None, failure=None, extra=()):
        from app.pipeline import spherical_to_brush as spherical
        import torchvision.models.segmentation as segmentation
        from test_cpu_regression import mask_weights, DeterministicPersonModel
        patches = [patch.dict(os.environ, {'MASK_DEVICE': 'cpu'}),
                   patch.object(segmentation, 'LRASPP_MobileNet_V3_Large_Weights', mask_weights()),
                   patch.object(segmentation, 'lraspp_mobilenet_v3_large', return_value=DeterministicPersonModel())]
        if predictor:
            patches.append(patch('app.runtime.mask_compute.MaskPredictor', predictor))
        if failure:
            patches.append(patch.object(post.CudaPostprocess, 'batch', side_effect=failure))
        patches.extend(extra)
        for item in patches:
            item.start()
        try:
            result = spherical.create_dynamic_person_masks(root, _gpu_worker=True)
        finally:
            for item in reversed(patches):
                item.stop()
        return result, {path.name: cv2.imread(str(path), cv2.IMREAD_UNCHANGED) for path in sorted((root / 'masks').glob('*.png'))}

    def test_cuda_branch_and_its_fallback_match_the_cpu_path(self):
        from test_cpu_regression import make_mask_fixture
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); make_mask_fixture(root)
            cpu_result, cpu_masks = self.run_masks(root)
            gpu_result, gpu_masks = self.run_masks(root, FakeCudaPredictor)
            failed_result, failed_masks = self.run_masks(root, FakeCudaPredictor, RuntimeError('CUDA error: illegal memory access'))
        self.assertEqual(cpu_result['mask_compute']['postprocess'], 'CPU')
        self.assertEqual(gpu_result['mask_compute']['postprocess'], 'CUDA')
        self.assertEqual(failed_result['mask_compute']['postprocess'], 'CPU')
        self.assertIn('illegal memory access', failed_result['mask_compute']['fallback_reasons'][0])
        self.assertFalse(gpu_result['mask_compute']['fallback_reasons'])
        for masks in (gpu_masks, failed_masks):
            self.assertEqual(masks.keys(), cpu_masks.keys())
            for name in cpu_masks:
                np.testing.assert_array_equal(masks[name], cpu_masks[name])
        for key in ('masked_images', 'mean_ignored_pixels_percent', 'mean_nadir_mask_percent'):
            self.assertEqual(gpu_result[key], cpu_result[key]); self.assertEqual(failed_result[key], cpu_result[key])

    def test_first_batch_mismatch_with_opencv_disables_cuda_postprocessing(self):
        from test_cpu_regression import make_mask_fixture
        wrong = lambda probabilities, size, nadirs: np.ones((len(nadirs), size[1], size[0]), np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); make_mask_fixture(root)
            _, cpu_masks = self.run_masks(root)
            result, masks = self.run_masks(root, FakeCudaPredictor, extra=[patch.object(post.CudaPostprocess, 'ignored', side_effect=wrong)])
        self.assertEqual(result['mask_compute']['postprocess'], 'CPU')
        self.assertIn('differ from OpenCV', result['mask_compute']['fallback_reasons'][0])
        for name in cpu_masks:
            np.testing.assert_array_equal(masks[name], cpu_masks[name])

    @unittest.skipUnless(torch.cuda.is_available(), 'CUDA not available')
    def test_real_cuda_masks_match_cpu_masks(self):
        from test_cpu_regression import make_mask_fixture
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); make_mask_fixture(root)
            _, cpu_masks = self.run_masks(root)
            with patch.dict(os.environ, {'MASK_DEVICE': 'cuda'}):
                from app.pipeline import spherical_to_brush as spherical
                import torchvision.models.segmentation as segmentation
                from test_cpu_regression import mask_weights, DeterministicPersonModel
                with patch.object(segmentation, 'LRASPP_MobileNet_V3_Large_Weights', mask_weights()), \
                        patch.object(segmentation, 'lraspp_mobilenet_v3_large', return_value=DeterministicPersonModel()):
                    result = spherical.create_dynamic_person_masks(root, _gpu_worker=True)
            gpu_masks = {path.name: cv2.imread(str(path), cv2.IMREAD_UNCHANGED) for path in sorted((root / 'masks').glob('*.png'))}
        self.assertEqual(result['mask_compute']['postprocess'], 'CUDA')
        for name in cpu_masks:
            np.testing.assert_array_equal(gpu_masks[name], cpu_masks[name])


if __name__ == '__main__':
    unittest.main()
