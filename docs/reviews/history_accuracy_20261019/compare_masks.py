"""Read-only comparison of saved training masks and the pre-18 October recipe."""
import json
import math
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision.models.segmentation import LRASPP_MobileNet_V3_Large_Weights, lraspp_mobilenet_v3_large

from app.pipeline.pano_to_views import perspective_map
from app.runtime import mask_postprocess

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
OLD = ROOT / 'web_data/jobs/d36645f4d5/brush_data'
NEW = ROOT / 'web_data/jobs/8cc360d5d3/brush_data'
SAMPLES = [(0, 0), (900, 0), (1800, 0), (3000, 0), (4200, 0),
           (900, 4), (1800, 4), (3000, 4), (4200, 4), (1800, 9), (3000, 10), (4200, 11)]


def legacy(probability, size, nadir):
    width, height = size
    probability = cv2.resize(probability, (width, height), interpolation=cv2.INTER_LINEAR)
    ignored = (probability > 0.05).astype(np.uint8)
    ignored = np.maximum(ignored, nadir)
    ignored = cv2.morphologyEx(ignored, cv2.MORPH_CLOSE, np.ones((21, 21), dtype=np.uint8))
    ignored = cv2.dilate(ignored, np.ones((15, 15), dtype=np.uint8))
    contours, _ = cv2.findContours(ignored, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if cv2.contourArea(contour) > width * height * 0.01 and y + box_height > height * 0.65:
            cv2.fillConvexPoly(ignored, cv2.convexHull(contour), 1)
            cv2.rectangle(ignored, (max(0, x - 12), y), (min(width - 1, x + box_width + 12), height - 1), 1)
    return np.where(ignored > 0, 0, 255).astype(np.uint8)


def overlay(image, mask):
    image = cv2.resize(image, (256, 256), interpolation=cv2.INTER_AREA)
    invalid = cv2.resize(mask, (256, 256), interpolation=cv2.INTER_NEAREST) == 0
    image[invalid] = (image[invalid] * .35 + np.array([40, 40, 230]) * .65).astype(np.uint8)
    return image


def main():
    torch.set_num_threads(2)
    cv2.setNumThreads(2)
    metadata = json.loads((NEW / 'transforms.json').read_text(encoding='utf8'))
    by_name = {Path(frame['file_path']).stem: frame for frame in metadata['frames']}
    width = int(metadata['w'])
    fov = math.degrees(2 * math.atan(width / (2 * float(metadata['fl_x']))))
    weights = LRASPP_MobileNet_V3_Large_Weights.DEFAULT
    model = lraspp_mobilenet_v3_large(weights=weights).eval()
    transform = weights.transforms()
    records, rows = [], []
    for index, view in SAMPLES:
        name = f'frame_{index:06d}_view_{view:02d}'
        old_image = cv2.imread(str(OLD / 'images' / (name + '.jpg')))
        new_image = cv2.imread(str(NEW / 'images' / (name + '.jpg')))
        old_mask = cv2.imread(str(OLD / 'masks' / (name + '.png')), cv2.IMREAD_GRAYSCALE)
        new_mask = cv2.imread(str(NEW / 'masks' / (name + '.png')), cv2.IMREAD_GRAYSCALE)
        if any(value is None for value in (old_image, new_image, old_mask, new_mask)):
            records.append(dict(name=name, error='missing image/mask'))
            continue
        height, width = new_image.shape[:2]
        with Image.open(NEW / 'images' / (name + '.jpg')) as image:
            inputs = transform(image.convert('RGB')).unsqueeze(0)
        with torch.inference_mode():
            probability = model(inputs)['out'].softmax(1)[0, 15].numpy()
        pitch = float(by_name[name].get('pitch_deg', 0))
        latitude = perspective_map(width, height, fov, 0., pitch)[1]
        nadir = (latitude > (height - 1) * .72).astype(np.uint8)
        before = legacy(probability, (width, height), nadir)
        after = mask_postprocess.valid_mask(mask_postprocess.fill_lower_components(
            mask_postprocess.cpu_ignored(probability, (width, height), nadir)))[0]
        old_at_new_size = cv2.resize(old_mask, (width, height), interpolation=cv2.INTER_NEAREST)
        record = dict(name=name, legacy_vs_current_differing_pixels=int(np.count_nonzero(before != after)),
                      cpu_vs_saved_directml_differing_percent=round(100 * float(np.mean(after != new_mask)), 6),
                      saved_old_ignored_percent=round(100 * float(np.mean(old_mask == 0)), 3),
                      saved_new_ignored_percent=round(100 * float(np.mean(new_mask == 0)), 3),
                      saved_old_vs_new_differing_percent=round(100 * float(np.mean(old_at_new_size != new_mask)), 3))
        records.append(record)
        panels = [cv2.resize(new_image, (256, 256)), overlay(old_image, old_mask),
                  overlay(new_image, new_mask), overlay(new_image, after)]
        titles = ['New input 1024', 'Old saved mask 1536', 'New saved DirectML', 'Current CPU mask']
        for panel, title in zip(panels, titles):
            cv2.rectangle(panel, (0, 0), (255, 38), (20, 20, 20), -1)
            cv2.putText(panel, title, (5, 15), cv2.FONT_HERSHEY_SIMPLEX, .4, (255, 255, 255), 1)
            cv2.putText(panel, name, (5, 30), cv2.FONT_HERSHEY_SIMPLEX, .37, (255, 255, 255), 1)
        rows.append(np.hstack(panels))
        print(json.dumps(record), flush=True)
    (OUTPUT / 'mask_comparison.json').write_text(json.dumps(records, indent=2), encoding='utf8')
    for start in range(0, len(rows), 4):
        cv2.imwrite(str(OUTPUT / f'mask_comparison_{start // 4 + 1}.jpg'), np.vstack(rows[start:start+4]))


if __name__ == '__main__':
    main()
