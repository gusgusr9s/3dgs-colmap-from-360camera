"""Numerically compare the Brush 0.3 and Three r186 opacity conventions."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.spatial.transform import Rotation
from plyfile import PlyData
from app.web import server as app


def inspect(model):
    info = app.model_info(app.MODELS / model)
    dataset = app.JOBS / info['job'] / 'model_runs' / info['run_id'] / 'dataset'
    if not (dataset / 'transforms.json').is_file():
        dataset = app.JOBS / info['job'] / 'brush_data'
    data = app.read_json(dataset / 'transforms.json')
    candidates = [f for f in data['frames'] if abs(float(f.get('pitch_deg', 0))) < 1e-6]
    frame = app.model_viewer_config(model) or candidates[len(candidates) // 2]
    pose = np.asarray(frame['transform_matrix'])
    v = PlyData.read(app.MODELS / model, mmap=True)['vertex'].data
    v = v[::max(1, len(v) // 200000)]
    xyz = np.column_stack([v[n] for n in ('x', 'y', 'z')])
    local = (xyz - pose[:3, 3]) @ pose[:3, :3]
    depth = -local[:, 2]
    fx, fy = data['fl_x'], data['fl_y']
    width, height = data['w'], data['h']
    inside = (depth > .01) & (np.abs(local[:, 0]) < depth * width / (2 * fx)) & (np.abs(local[:, 1]) < depth * height / (2 * fy))
    v, local = v[inside], local[inside]
    q = np.column_stack([v[f'rot_{i}'] for i in (1, 2, 3, 0)])
    rotations = Rotation.from_quat(q).as_matrix()
    scale = np.exp(np.column_stack([v[f'scale_{i}'] for i in range(3)]))
    rs = rotations * scale[:, None, :]
    cov = rs @ np.transpose(rs, (0, 2, 1))
    cov = pose[:3, :3].T @ cov @ pose[:3, :3]
    result = {'model': model, 'visible_sample_splats': len(v), 'quantiles': [0, 10, 50, 90, 100]}
    opac = 1 / (1 + np.exp(-np.clip(v['opacity'], -30, 30)))
    for enlargement in (1, 8):
        j = np.zeros((len(v), 2, 3))
        j[:, 0, 0] = -fx * enlargement / local[:, 2]
        j[:, 1, 1] = -fy * enlargement / local[:, 2]
        j[:, 0, 2] = fx * enlargement * local[:, 0] / local[:, 2] ** 2
        j[:, 1, 2] = fy * enlargement * local[:, 1] / local[:, 2] ** 2
        screen = j @ cov @ np.transpose(j, (0, 2, 1))
        a, b, c = screen[:, 0, 0], screen[:, 0, 1], screen[:, 1, 1]
        factor = np.sqrt(np.maximum(0, (a * c - b * b) / np.maximum((a + .3) * (c + .3) - b * b, 1e-6)))
        significant = opac > .1
        result[f'opacity_compensation_at_{enlargement}x'] = {
            'native_alpha_multiplier': np.percentile(factor, [0, 10, 50, 90, 100]).tolist(),
            'significant_splats_losing_over_half_opacity_percent': float(np.mean(factor[significant] < .5) * 100),
            'brush_multiplier': 1,
        }
    return result


if __name__ == '__main__':
    models = ['49611799da_20261008_031339_034c0a77.ply', 'a147ed6bb2_20261008_012920_c9975336.ply']
    results = [inspect(name) for name in models]
    report = {'method': 'Actual PLY covariance and captured camera projection; not a browser render.',
              'references': ['https://raw.githubusercontent.com/ArthurBrussee/brush/v0.3.0/crates/brush-render/src/shaders/project_visible.wgsl',
                             'https://raw.githubusercontent.com/mrdoob/three.js/r186/examples/jsm/objects/GaussianSplat.js',
                             'https://raw.githubusercontent.com/mrdoob/three.js/r186/examples/webgpu_gaussian_splat.html'],
              'models': results, 'encoded_0_5_rgb_with_incorrect_linear_to_srgb_conversion': .73535698}
    app.write_json(app.ROOT / 'web_data/quality_diagnostics/renderer_compatibility.json', report)
    print(json.dumps(report, indent=2))
