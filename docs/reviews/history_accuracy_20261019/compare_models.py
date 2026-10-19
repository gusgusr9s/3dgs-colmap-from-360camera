"""Compare the two requested models using their existing held-out Brush renders."""
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
CASES = [('Ultra / every 4 frames', 'd36645f4d5', '20261017_235051_ebed1a16', 30000),
         ('High / every 10 frames', '8cc360d5d3', '20261019_001635_dab76b76', 20000)]
SAMPLES = [(900, 0), (1800, 0), (3000, 0), (4200, 4), (1800, 4), (4200, 11)]


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def load(path, flag=cv2.IMREAD_COLOR):
    image = cv2.imread(str(path), flag)
    if image is None:
        raise ValueError('Cannot read ' + str(path))
    return image


def score(reference, image, mask):
    count = cv2.countNonZero(mask)
    if count < reference.shape[0] * reference.shape[1] * .01:
        return None
    mse = cv2.norm(reference, image, cv2.NORM_L2SQR, mask) / (count * 3)
    return float(10 * math.log10(255 ** 2 / max(mse, 1e-9)))


def figure(names, cases, file):
    side, gap, label = 384, 10, 45
    columns = ['Same source frame', CASES[0][0], CASES[1][0], 'Shared evaluation mask']
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 19)
    small = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 16)
    canvas = Image.new('RGB', (4 * (side + gap) + gap, len(names) * (side + label + gap) + 52), '#101827')
    draw = ImageDraw.Draw(canvas)
    draw.text((gap, 12), 'Existing held-out renders; same frame IDs and view angles; red = excluded from scoring', fill='white', font=font)
    for row, stem in enumerate(names):
        frame = cases[0]['frames'][stem]
        source = load(cases[0]['dataset'] / frame['file_path'])
        masks = [load(case['dataset'] / 'masks' / (stem + '.png'), cv2.IMREAD_GRAYSCALE) for case in cases]
        valid = cv2.bitwise_and(masks[0], cv2.resize(masks[1], masks[0].shape[::-1], interpolation=cv2.INTER_NEAREST))
        overlay = source.copy()
        overlay[valid == 0] = (overlay[valid == 0] * .35 + np.array([40, 40, 230]) * .65).astype(np.uint8)
        panels = [source, *[load(case['renders'] / (stem + '.png')) for case in cases], overlay]
        for column, (title, panel) in enumerate(zip(columns, panels)):
            x, y = gap + column * (side + gap), 52 + row * (side + label + gap)
            draw.text((x, y), title, fill='white', font=font)
            draw.text((x, y + 23), stem, fill='#b8c7df', font=small)
            canvas.paste(Image.fromarray(cv2.cvtColor(cv2.resize(panel, (side, side), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)), (x, y + label))
    canvas.save(file)


def main():
    started = time.monotonic()
    cv2.setNumThreads(2)
    cases = []
    fingerprints = []
    for label, job, run, step in CASES:
        root = ROOT / 'web_data/jobs' / job / 'model_runs' / run
        dataset = root / 'dataset'
        metadata = read(dataset / 'training_input/transforms_val.json')
        status = read(root / 'status.json')
        fingerprints.append(status['source_profile']['fingerprint'])
        frames = {Path(frame['file_path']).stem: frame for frame in metadata['frames']}
        cases.append(dict(label=label, dataset=dataset, frames=frames, renders=root / 'brush_export' / f'eval_{step}'))
    if len(set(fingerprints)) != 1 or set(cases[0]['frames']) != set(cases[1]['frames']):
        raise ValueError('Source fingerprint or held-out frames differ')
    names = sorted(cases[0]['frames'])
    for stem in names:
        for case in cases:
            if not (case['renders'] / (stem + '.png')).is_file():
                raise ValueError('Missing render: ' + stem)
    selected = [f'frame_{index:06d}_view_{view:02d}' for index, view in SAMPLES]
    figure(selected[:3], cases, OUTPUT / 'requested_models_comparison_1.jpg')
    figure(selected[3:], cases, OUTPUT / 'requested_models_comparison_2.jpg')
    print('Figures ready; scoring ' + str(len(names)) + ' matching held-out views', flush=True)
    records = []
    for ordinal, stem in enumerate(names):
        frame = cases[0]['frames'][stem]
        reference = load(cases[0]['dataset'] / frame['file_path'])
        height, width = reference.shape[:2]
        masks = [load(case['dataset'] / 'masks' / (stem + '.png'), cv2.IMREAD_GRAYSCALE) for case in cases]
        shared = cv2.bitwise_and(masks[0], cv2.resize(masks[1], (width, height), interpolation=cv2.INTER_NEAREST))
        scores = []
        for case in cases:
            rendered = load(case['renders'] / (stem + '.png'))
            if rendered.shape != reference.shape:
                rendered = cv2.resize(rendered, (width, height), interpolation=cv2.INTER_LINEAR)
            scores.append(score(reference, rendered, shared))
        records.append(dict(stem=stem, frame_index=frame['frame_index'], yaw=frame['yaw_deg'], pitch=frame['pitch_deg'],
                            valid_percent=round(100 * cv2.countNonZero(shared) / shared.size, 4), ultra_psnr=scores[0], high_psnr=scores[1]))
        if (ordinal + 1) % 160 == 0:
            print(f'Scored {ordinal+1}/{len(names)} in {time.monotonic()-started:.1f}s', flush=True)
    valid = [row for row in records if row['ultra_psnr'] is not None]
    def summarize(rows):
        return dict(views=len(rows), ultra_mean_psnr_db=round(float(np.mean([row['ultra_psnr'] for row in rows])), 4),
                    high_mean_psnr_db=round(float(np.mean([row['high_psnr'] for row in rows])), 4),
                    ultra_better_views=sum(row['ultra_psnr'] > row['high_psnr'] for row in rows))
    summary = dict(models=[dict(label=label, job=job, run=run) for label, job, run, _ in CASES],
                   reference='Original Ultra 1536x1536 input at the same held-out frame IDs and crop angles; High renders linearly upsampled to 1536.',
                   masks='Intersection of both runs\' masks; same valid pixels for both scores; views with <1% valid pixels excluded.',
                   source_fingerprint=fingerprints[0], common_views=len(names), common_positions=len({row['frame_index'] for row in records}),
                   scored=summarize(valid), horizontal=summarize([row for row in valid if row['pitch'] == 0]),
                   limitation='Each model uses its own estimated poses. Profile, sampling, splat budget and mapping method differ; this compares outputs without isolating their causal effects. Masked PSNR does not measure removal of people or novel-view geometry.',
                   elapsed_seconds=round(time.monotonic()-started, 2))
    (OUTPUT / 'requested_models_metrics.json').write_text(json.dumps(dict(summary=summary, views=records), indent=2), encoding='utf8')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
