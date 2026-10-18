"""Compute image metrics only for the selected view, with file-aware caching."""
from functools import lru_cache

import cv2
import numpy as np


def signature(path):
    if path is None:
        return None
    stat = path.stat()
    return str(path), stat.st_size, stat.st_mtime_ns


def decode(filename, grayscale=False):
    pixels = np.fromfile(filename, dtype=np.uint8)
    image = cv2.imdecode(pixels, cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('Cannot decode comparison image')
    return image


def psnr(target, rendered, mask):
    rendered = cv2.resize(rendered, (target.shape[1], target.shape[0]))
    if mask is not None:
        mask = cv2.resize(mask, (target.shape[1], target.shape[0]), interpolation=cv2.INTER_NEAREST)
    valid = mask > 127 if mask is not None else np.ones(target.shape[:2], bool)
    if not np.any(valid):
        return None
    mse = float(np.mean((target.astype(np.float32) - rendered.astype(np.float32))[valid] ** 2))
    return 99.0 if mse == 0 else float(10 * np.log10(255 ** 2 / mse))


@lru_cache(maxsize=256)
def _metrics(source, rendered, mask, previous):
    target = decode(source[0])
    mask_image = decode(mask[0], grayscale=True) if mask else None
    coverage = float(np.mean(mask_image > 127)) * 100 if mask_image is not None else 100.0
    return {'psnr': psnr(target, decode(rendered[0]), mask_image), 'valid_pixels_percent': round(coverage, 2),
            'previous_psnr': psnr(target, decode(previous[0]), mask_image) if previous else None}


def frame_metrics(paths):
    return dict(_metrics(*(signature(paths.get(key)) for key in ('source', 'render', 'mask', 'previous'))))
