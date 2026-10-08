"""Measure actual input/render detail, camera exposure and exported Gaussian sizes."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from plyfile import PlyData
import web_app as app


def image_metrics(target, rendered, mask):
    if target.shape != rendered.shape:
        rendered = cv2.resize(rendered, (target.shape[1], target.shape[0]))
    valid = mask > 127 if mask is not None else np.ones(target.shape[:2], bool)
    # Keep masked moving subjects out of the neighborhood of detail measurements.
    inner = cv2.erode(valid.astype(np.uint8), np.ones((11, 11), np.uint8)) > 0
    if not inner.any():
        return None
    gray = [cv2.cvtColor(x, cv2.COLOR_BGR2GRAY).astype(np.float32) for x in (target, rendered)]
    mse = float(np.mean((target.astype(np.float32) - rendered.astype(np.float32))[valid] ** 2))
    edges = [np.hypot(cv2.Sobel(x, cv2.CV_32F, 1, 0), cv2.Sobel(x, cv2.CV_32F, 0, 1)) for x in gray]
    strong = inner & (edges[0] >= 30)
    laplacian = [float(np.var(cv2.Laplacian(x, cv2.CV_32F)[inner])) for x in gray]
    mx, my = [cv2.GaussianBlur(x, (11, 11), 1.5) for x in gray]
    vx = cv2.GaussianBlur(gray[0] ** 2, (11, 11), 1.5) - mx ** 2
    vy = cv2.GaussianBlur(gray[1] ** 2, (11, 11), 1.5) - my ** 2
    cov = cv2.GaussianBlur(gray[0] * gray[1], (11, 11), 1.5) - mx * my
    ssim = ((2 * mx * my + 6.5025) * (2 * cov + 58.5225)) / ((mx ** 2 + my ** 2 + 6.5025) * (vx + vy + 58.5225))
    return {
        'psnr_db': round(99 if mse == 0 else 10 * np.log10(255 ** 2 / mse), 3),
        'ssim_valid_inner': round(float(np.mean(ssim[inner])), 5),
        'source_laplacian_variance': round(laplacian[0], 3),
        'render_laplacian_variance': round(laplacian[1], 3),
        'edge_retention': round(float(np.mean(edges[1][strong]) / np.mean(edges[0][strong])), 4) if strong.any() else None,
        'valid_pixels_percent': round(float(valid.mean()) * 100, 3),
    }


def gaussian_stats(path, dataset):
    v = PlyData.read(path, mmap=True)['vertex'].data
    xyz = np.column_stack([v[n] for n in ('x', 'y', 'z')])
    scales = np.sort(np.exp(np.clip(np.column_stack([v[f'scale_{i}'] for i in range(3)]), -30, 30)), axis=1)
    tangential = scales[:, 2] / np.maximum(scales[:, 1], 1e-12)
    opacity = 1 / (1 + np.exp(-np.clip(v['opacity'], -30, 30)))
    meta = app.read_json(dataset / 'transforms.json')
    centers = np.asarray([f['transform_matrix'] for f in meta['frames']])[:, :3, 3]
    quantiles = [50, 90, 95, 99, 99.9, 100]
    return {
        'count': len(v), 'quantiles': quantiles,
        'max_axis': np.percentile(scales[:, 2], quantiles).tolist(),
        'longest_to_middle_axis': np.percentile(tangential, quantiles).tolist(),
        'needle_over_10': int((tangential > 10).sum()),
        'needle_over_20': int((tangential > 20).sum()),
        'opacity_below_0_05': int((opacity < .05).sum()),
        'camera_path_span': float(np.linalg.norm(np.ptp(centers, axis=0))),
        'camera_center_min': centers.min(axis=0).tolist(),
        'camera_center_max': centers.max(axis=0).tolist(),
        'splat_center_median': np.median(xyz, axis=0).tolist(),
    }


def diagnose(job, run):
    directory = app.JOBS / job / 'model_runs' / run
    status = app.read_json(directory / 'status.json')
    dataset = directory / 'dataset'
    step = status['quality']['training_steps']
    meta = app.read_json(dataset / 'transforms.json')
    frames = meta['frames']
    by_stem = {Path(f['file_path']).stem: f for f in frames}
    images = []
    renders = sorted((directory / 'brush_export' / f'eval_{step}').glob('*.png'))
    for render in renders:
        frame = by_stem.get(render.stem)
        if frame is None:
            continue
        target = cv2.imread(str(dataset / frame['file_path']))
        mask = cv2.imread(str(dataset / 'masks' / (render.stem + '.png')), 0)
        metrics = image_metrics(target, cv2.imread(str(render)), mask)
        if metrics:
            images.append({'name': render.stem, **metrics})
    train_views = len(frames) - len(renders)
    summary = {
        'job': job, 'run_id': run, 'model': status['model'],
        'image_size': [meta['w'], meta['h']], 'training_views': train_views,
        'steps_per_training_view': round(step / max(1, train_views), 2),
        'mask_percent': status['quality'].get('mean_ignored_pixels_percent'),
        'render_metrics_mean': {k: round(float(np.mean([x[k] for x in images if x.get(k) is not None])), 4)
                                for k in ('psnr_db', 'ssim_valid_inner', 'edge_retention', 'source_laplacian_variance', 'render_laplacian_variance')},
        'gaussians': gaussian_stats(app.MODELS / status['model'], dataset),
        'images': images,
        'limits': ['Actual held-out Brush GPU renders, before PLY cleanup.',
                   'Detail scores use eroded valid masks; do not measure masked people or novel-view geometry.',
                   'Scene scale is relative; a long Gaussian alone is not proof of an artifact.'],
    }
    return summary


if __name__ == '__main__':
    batch = app.read_json(app.EXPERIMENTS / 'real360_20261008.json')
    output = []
    for case in batch['cases']:
        if case['state'] != 'done':
            continue
        result = diagnose(case['job'], case['run_id'])
        result['case'] = case['id']
        output.append(result)
        print(json.dumps({k:v for k,v in result.items() if k not in {'images','gaussians','limits'}}), flush=True)
        print(json.dumps(result['gaussians']), flush=True)
    app.write_json(app.ROOT / 'web_data/quality_diagnostics/baseline.json', output)
