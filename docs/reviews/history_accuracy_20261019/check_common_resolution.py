"""Audit the comparison at native High resolution and verify held-out identity."""
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
CASES = [('ultra', 'd36645f4d5', '20261017_235051_ebed1a16', 30000),
         ('high', '8cc360d5d3', '20261019_001635_dab76b76', 20000)]


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def pixels(path, mode=cv2.IMREAD_COLOR):
    image = cv2.imread(str(path), mode)
    if image is None:
        raise ValueError('Unreadable: ' + str(path))
    return image


def main():
    cv2.setNumThreads(2)
    started = time.monotonic()
    cases = []
    checks = []
    for label, job, run, steps in CASES:
        directory = ROOT / 'web_data/jobs' / job / 'model_runs' / run
        dataset = directory / 'dataset'
        train = read(dataset / 'training_input/transforms.json')
        held = read(dataset / 'training_input/transforms_val.json')
        train_ids = {f['frame_index'] for f in train['frames']}
        held_ids = {f['frame_index'] for f in held['frames']}
        frames = {Path(f['file_path']).stem: f for f in held['frames']}
        if train_ids & held_ids or len(frames) != len(held['frames']):
            raise ValueError('Overlapping split or duplicate frame names')
        status = read(directory / 'status.json')
        cases.append(dict(label=label, dataset=dataset, frames=frames, renders=directory / 'brush_export' / f'eval_{steps}',
                          fingerprint=status['source_profile']['fingerprint']))
        checks.append(dict(model=label, train_positions=len(train_ids), held_positions=len(held_ids),
                           held_views=len(frames), overlapping_train_eval_positions=0))
    if cases[0]['fingerprint'] != cases[1]['fingerprint'] or cases[0]['frames'].keys() != cases[1]['frames'].keys():
        raise ValueError('Different source or held-out images')
    names = sorted(cases[0]['frames'])
    for stem in names:
        for key in ('frame_index', 'yaw_deg', 'pitch_deg'):
            if cases[0]['frames'][stem][key] != cases[1]['frames'][stem][key]:
                raise ValueError('Different source frame/crop angle: ' + stem)
    print('Source, frame/crop identities and disjoint evaluation splits verified', flush=True)
    rows = []
    for i, stem in enumerate(names):
        frame = cases[1]['frames'][stem]
        reference = pixels(cases[1]['dataset'] / frame['file_path'])
        height, width = reference.shape[:2]
        if (width, height) != (1024, 1024):
            raise ValueError('Unexpected reference dimensions')
        masks = [pixels(case['dataset'] / 'masks' / (stem + '.png'), cv2.IMREAD_GRAYSCALE) for case in cases]
        shared = cv2.bitwise_and(cv2.resize(masks[0], (width, height), interpolation=cv2.INTER_NEAREST), masks[1])
        count = cv2.countNonZero(shared)
        scores = []
        errors = []
        for case in cases:
            render = pixels(case['renders'] / (stem + '.png'))
            if render.shape != reference.shape:
                render = cv2.resize(render, (width, height), interpolation=cv2.INTER_AREA)
            error = cv2.norm(reference, render, cv2.NORM_L2SQR, shared)
            errors.append(error)
            scores.append(10 * math.log10(255 ** 2 / max(error / (count * 3), 1e-9)) if count >= width * height * .01 else None)
        rows.append(dict(stem=stem, frame_index=frame['frame_index'], pitch=frame['pitch_deg'],
                         valid_pixels=count, ultra_psnr=scores[0], high_psnr=scores[1],
                         ultra_squared_error=errors[0], high_squared_error=errors[1]))
        if (i + 1) % 320 == 0:
            print(f'Checked {i+1}/{len(names)} in {time.monotonic()-started:.1f}s', flush=True)
    def summarize(selected):
        selected = [row for row in selected if row['ultra_psnr'] is not None]
        count = sum(row['valid_pixels'] for row in selected) * 3
        return dict(views=len(selected),
                    ultra_mean_view_psnr_db=round(float(np.mean([r['ultra_psnr'] for r in selected])), 4),
                    high_mean_view_psnr_db=round(float(np.mean([r['high_psnr'] for r in selected])), 4),
                    ultra_pooled_pixel_psnr_db=round(10*math.log10(255**2/(sum(r['ultra_squared_error'] for r in selected)/count)), 4),
                    high_pooled_pixel_psnr_db=round(10*math.log10(255**2/(sum(r['high_squared_error'] for r in selected)/count)), 4),
                    ultra_better_views=sum(r['ultra_psnr'] > r['high_psnr'] for r in selected))
    summary = dict(reference='High run source crops at native 1024px; Ultra renders downsampled with INTER_AREA; High renders unchanged.',
                   mask='Intersection of valid regions in both masks at 1024px, same pixels for both models.',
                   checks=checks, all_views=summarize(rows), horizontal=summarize([r for r in rows if r['pitch'] == 0]),
                   limitation='Sensitivity check for reference size/interpolation. Original models still differ in profile, sampling, poses and training budget. Neither PSNR summary measures removal of people.',
                   seconds=round(time.monotonic()-started, 2))
    (OUTPUT / 'common_1024_audit.json').write_text(json.dumps(dict(summary=summary, views=rows), indent=2), encoding='utf8')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
