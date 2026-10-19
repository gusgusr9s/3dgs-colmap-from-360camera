"""Exclude people from spherical COLMAP features using overlapping pinhole views.

COLMAP expects masks named <image-name>.png; black pixels exclude keypoints.
Inference stays on CPU, including when a temporary map overlaps GPU training.
"""
from contextlib import contextmanager
import math
from pathlib import Path

import cv2
import numpy as np

from app.jobs.task_control import check_stop, write
from app.pipeline.pano_to_views import perspective_map, project_frame
from app.runtime.cpu_runtime import cpu_budget

REPORT = 'person_masking.json'
RECIPE = 'pitched_people_v1'
SIDE, FOV, PITCH, THRESHOLD = 384, 120, -20, .25
YAWS = tuple(range(0, 360, 60))


@contextmanager
def predictor(environment):
    import torch
    from PIL import Image
    from torchvision.models.segmentation import LRASPP_MobileNet_V3_Large_Weights, lraspp_mobilenet_v3_large
    previous = torch.get_num_threads()
    torch.set_num_threads(min(4, cpu_budget(environment).threads))
    try:
        weights = LRASPP_MobileNet_V3_Large_Weights.DEFAULT
        model = lraspp_mobilenet_v3_large(weights=weights).eval()
        transform = weights.transforms(resize_size=SIDE)

        def predict(crops):
            tensors = [transform(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))) for crop in crops]
            with torch.inference_mode():
                return model(torch.stack(tensors))['out'].softmax(1)[:, 15].numpy()

        yield predict
    finally:
        torch.set_num_threads(previous)


def inverse_maps(width, height, yaw):
    """Map equirectangular pixels back into the matching pinhole image."""
    longitude = (np.arange(width, dtype=np.float32)/(width-1)-.5)*(2*np.pi)
    latitude = (.5-np.arange(height, dtype=np.float32)/(height-1))*np.pi
    delta = longitude[None, :] - math.radians(yaw)
    z = np.cos(latitude[:, None])*np.cos(delta)
    x = np.cos(latitude[:, None])*np.sin(delta)
    vertical = np.broadcast_to(np.sin(latitude[:, None]), z.shape)
    pitch = math.radians(PITCH)
    y = vertical*math.cos(pitch)+z*math.sin(pitch)
    depth = -vertical*math.sin(pitch)+z*math.cos(pitch)
    focal = .5*SIDE/math.tan(math.radians(FOV)/2)
    denominator = np.maximum(depth, 1e-8)
    mx = (focal*x/denominator+(SIDE-1)/2).astype(np.float32)
    my = (-focal*y/denominator+(SIDE-1)/2).astype(np.float32)
    inside = (depth > 0) & (mx >= 0) & (mx <= SIDE-1) & (my >= 0) & (my <= SIDE-1)
    mx[~inside] = my[~inside] = -1
    return mx, my


def valid_mask(probabilities, maps, size):
    width, height = size
    ignored = np.zeros(maps[0][0].shape, np.uint8)
    for probability, (mx, my) in zip(probabilities, maps):
        probability = cv2.resize(np.asarray(probability, np.float32), (SIDE, SIDE), interpolation=cv2.INTER_LINEAR)
        mapped = cv2.remap(probability, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        ignored |= (mapped >= THRESHOLD).astype(np.uint8)
    # Wrap horizontally so a person at the panorama seam is masked on both sides.
    padded = np.pad(ignored, ((0, 0), (2, 2)), mode='wrap')
    ignored = cv2.dilate(padded, np.ones((5, 5), np.uint8))[:, 2:-2]
    mask = cv2.resize(255-ignored*255, (width, height), interpolation=cv2.INTER_NEAREST)
    return mask


def create(panorama_dir, output_dir, environment, progress=None, names=None):
    panorama_dir, output_dir = Path(panorama_dir), Path(output_dir)
    paths = sorted(panorama_dir/name for name in names) if names is not None else sorted(panorama_dir.glob('*.jpg'))
    if not paths:
        raise ValueError('ไม่พบ panorama สำหรับ Mask คนก่อนหา Camera Pose')
    output_dir.mkdir(parents=True, exist_ok=True)
    # Look slightly upward: a nearby operator's head can be well above the
    # horizon. 120 degrees also reaches the existing camera-mask floor cutoff.
    projection = [perspective_map(SIDE, SIDE, FOV, yaw, PITCH)[:2] for yaw in YAWS]
    maps, previous_size, ignored_total, combined_total = None, None, 0., 0.
    with predictor(environment) as predict:
        for ordinal, path in enumerate(paths):
            check_stop()
            if path.parent.resolve() != panorama_dir.resolve() or not path.is_file():
                raise ValueError('พาธ panorama สำหรับ Mask ไม่ถูกต้อง')
            image = cv2.imread(str(path))
            if image is None:
                raise ValueError(f'อ่าน panorama สำหรับ Mask ไม่ได้: {path.name}')
            height, width = image.shape[:2]
            if previous_size != (width, height):
                canvas_width = min(960, width)
                canvas_height = max(2, round(height*canvas_width/width))
                maps = [inverse_maps(canvas_width, canvas_height, yaw) for yaw in YAWS]
                previous_size = (width, height)
            crops = [project_frame(image, SIDE, SIDE, FOV, yaw, PITCH, maps=projection[i]) for i, yaw in enumerate(YAWS)]
            probabilities = np.concatenate([predict(crops[i:i+3]) for i in range(0, len(crops), 3)])
            mask = valid_mask(probabilities, maps, (width, height))
            destination = output_dir/(path.name+'.png')
            if not cv2.imwrite(str(destination), mask):
                raise RuntimeError(f'บันทึก Mask คนก่อนหา pose ไม่ได้: {path.name}')
            ignored_total += float(np.mean(mask == 0))
            combined = mask.copy()
            combined[int(height*.72):] = 0
            combined_total += float(np.mean(combined == 0))
            if progress:
                progress(ordinal+1, len(paths))
    report = dict(recipe=RECIPE, images=len(paths), backend='CPU', threshold=THRESHOLD,
                  model='LRASPP MobileNetV3', views_per_panorama=len(YAWS),
                  person_ignored_pixels_percent=round(100*ignored_total/len(paths), 3),
                  combined_ignored_pixels_percent=round(100*combined_total/len(paths), 3))
    write(output_dir.parent/REPORT, report)
    return report
