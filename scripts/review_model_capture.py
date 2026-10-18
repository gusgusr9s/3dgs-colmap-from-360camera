"""Review real held-out renders; preserve source video, model and training artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from app.web import server as app
from app.models.quality_metrics import psnr
from app.pipeline.pano_to_views import frames_from_input, project_frame


def review(model, output):
    info = app.model_info(app.MODELS / model)
    run = app.JOBS / info['job'] / 'model_runs' / info['run_id']
    dataset = run / 'dataset'
    metadata = app.read_json(dataset / 'transforms.json')
    frames = {Path(frame['file_path']).stem: frame for frame in metadata['frames']}
    rendered = run / 'brush_export' / f"eval_{info['quality']['training_steps']}"
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(rendered.glob('*.png')):
        stem = path.stem.removesuffix('.jpg').removesuffix('.jpeg')
        frame = frames.get(stem)
        if frame is None:
            continue
        target = cv2.imread(str(dataset / frame['file_path']))
        image = cv2.imread(str(path))
        mask = cv2.imread(str(dataset / 'masks' / (stem + '.png')), cv2.IMREAD_GRAYSCALE)
        if target is None or image is None:
            continue
        valid_fraction = float(np.mean(mask > 127)) if mask is not None else 1.
        rows.append(dict(name=stem, frame_index=frame['frame_index'], yaw=frame.get('yaw_deg', 0),
            pitch=frame.get('pitch_deg', 0), valid_fraction=valid_fraction,
            masked_psnr=psnr(target, image, mask) if valid_fraction else None,
            full_psnr=psnr(target, image, None)))
    valid = [row for row in rows if row['masked_psnr'] is not None]
    if not valid:
        raise ValueError('No render with valid unmasked pixels is available for review')
    values = [row['masked_psnr'] for row in valid]
    summary = dict(rendered_views=len(rows), evaluated_views=len(valid),
        fully_masked_views=len(rows)-len(valid), mean_masked_psnr=float(np.mean(values)),
        median_masked_psnr=float(np.median(values)), minimum_masked_psnr=min(values),
        p10_masked_psnr=float(np.percentile(values, 10)), maximum_masked_psnr=max(values),
        mean_full_psnr=float(np.mean([row['full_psnr'] for row in rows])),
        valid_pixels_mean_percent=float(np.mean([row['valid_fraction'] for row in rows]) * 100))
    horizontal = sorted((row for row in valid if row['pitch'] == 0), key=lambda row: row['frame_index'])
    horizontal = horizontal or valid
    selected = [horizontal[round(index * (len(horizontal)-1) / 3)] for index in range(4)]
    selected += sorted((row for row in valid if row['valid_fraction'] > .3), key=lambda row: row['masked_psnr'])[:2]
    for pitch in (-55, 55):
        candidates = [row for row in valid if row['pitch'] == pitch]
        if candidates:
            selected.append(min(candidates, key=lambda row: row['masked_psnr']))
    selected = list({row['name']: row for row in selected}.values())
    source = app.source_from_key(info['source_key'])
    if source is None:
        raise ValueError('Original video is missing')
    font_path = Path('C:/Windows/Fonts/arial.ttf')
    font = ImageFont.truetype(str(font_path), 17) if font_path.is_file() else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 14) if font_path.is_file() else font
    size, gap, title = 320, 12, 68
    sheet = Image.new('RGB', (4*size+5*gap, len(selected)*(size+title)+gap), '#101827')
    draw = ImageDraw.Draw(sheet)
    projection_settings = info.get('settings', {})
    source_matches = []
    for row_index, row in enumerate(selected):
        frame = frames[row['name']]
        _, original = next(frames_from_input(source, 1, row['frame_index'], row['frame_index'] + 1))
        crop = project_frame(original, metadata['w'], metadata['h'], projection_settings.get('fov', 90), row['yaw'], row['pitch'])
        target = cv2.imread(str(dataset / frame['file_path']))
        result = cv2.imread(str(rendered / (row['name'] + '.png')))
        result = cv2.resize(result, (target.shape[1], target.shape[0]))
        mask = cv2.imread(str(dataset / 'masks' / (row['name'] + '.png')), cv2.IMREAD_GRAYSCALE)
        source_matches.append(dict(name=row['name'], original_vs_training_psnr=psnr(crop, target, None)))
        cv2.imwrite(str(output / (row['name'] + '_original_crop.png')), crop)
        delta = np.mean(np.abs(target.astype(np.float32) - result.astype(np.float32)), axis=2)
        heatmap = cv2.applyColorMap(np.clip(delta*4, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        if mask is not None:
            heatmap[mask <= 127] = (60, 60, 60)
        y = gap + row_index*(size+title)
        draw.text((gap,y), f"{row['name']} | yaw {row['yaw']:g}, pitch {row['pitch']:g} | PSNR masked {row['masked_psnr']:.2f} dB | valid {row['valid_fraction']*100:.1f}%", fill='white', font=font)
        for column, (label, pixels) in enumerate(zip(('Original 8K projection', 'Training input', 'Brush held-out render', 'Error x4; masked = grey'), (crop, target, result, heatmap))):
            x = gap + column*(size+gap)
            draw.text((x, y+27), label, fill='#c5d1e2', font=small)
            image = Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)).resize((size,size), Image.Resampling.LANCZOS)
            sheet.paste(image, (x, y+title))
    sheet.save(output / 'capture_comparison.png')
    report = dict(model=model, job=info['job'], run=info['run_id'], summary=summary,
        quality=info['quality'], selected=selected, source_checks=source_matches, views=rows,
        method='Original 8K video decoded at exact frame IDs and projected with original yaw/pitch/FOV; existing held-out Brush renders, no retraining.',
        limits=['Held-out cameras lie along the captured trajectory; this does not verify arbitrary novel viewpoints.',
                'Black mask pixels are excluded from masked PSNR; full-image metrics are reported separately.',
                'These are Brush checkpoint renders, not browser screenshots of the published PLY.'])
    digests = []
    for path in (app.MODELS / model, run / 'brush_export/splat.ply'):
        with path.open('rb') as handle:
            digests.append(hashlib.file_digest(handle, 'sha256').hexdigest())
    report['published_model_sha256'] = digests[0]
    report['published_matches_evaluated_checkpoint'] = digests[0] == digests[1]
    (output / 'review.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)
    print(output / 'capture_comparison.png', flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    review(args.model, args.output)
