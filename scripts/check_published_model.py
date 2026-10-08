"""Render a published PLY through Brush with one effectively frozen GPU step.

This checks export/cleanup independently of the browser. It is not a browser
capture, nor an exact zero-step render: every learning rate is 1e-20, growth
and noise are disabled, and parameter drift is measured after the round trip.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from plyfile import PlyData
import web_app as app


def check(model):
    info, original_dataset, _ = app.refinement_input(model)
    if any(app.task_is_active(d.name) for d in app.JOBS.iterdir() if d.is_dir()):
        raise RuntimeError('Wait for the active trainer before running the GPU render probe')
    parent_run = original_dataset.parent
    probe = app.ROOT / 'web_data/quality_diagnostics' / ('published_' + Path(model).stem)
    if probe.exists():
        raise RuntimeError('A probe already exists: ' + str(probe))
    probe.mkdir(parents=True)
    source = app.MODELS / model
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    def copy_immutable(source, target):
        if Path(source).suffix.lower() != '.json':
            try:
                os.link(source, target)
                return target
            except OSError:
                pass
        return shutil.copy2(source, target)

    dataset = probe / 'dataset'
    shutil.copytree(original_dataset, dataset, copy_function=copy_immutable)
    copy_immutable(source, dataset / 'published_init.ply')
    data = app.read_json(dataset / 'transforms.json')
    data['ply_file_path'] = 'published_init.ply'
    app.write_json(dataset / 'transforms.json', data)
    export = probe / 'brush_export'
    export.mkdir()
    command = [str(app.brush_cli()), str(dataset), '--total-steps', '1',
               '--max-resolution', str(max(data['w'], data['h'])),
               '--max-splats', '10000000', '--growth-stop-iter', '0',
               '--refine-every', '999999', '--mean-noise-weight', '0',
               '--aux-loss-time', '0', '--eval-split-every', '17',
               '--eval-every', '1', '--eval-save-to-disk',
               '--export-every', '1', '--export-path', str(export), '--export-name', 'probe.ply']
    for option in ['lr-mean', 'lr-mean-end', 'lr-scale', 'lr-scale-end',
                   'lr-coeffs-dc', 'lr-opac', 'lr-rotation']:
        command.extend(['--' + option, '1e-20'])
    with (probe / 'probe.log').open('w', encoding='utf-8') as log:
        log.write('$ ' + ' '.join(command) + '\n')
        log.flush()
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True,
                       cwd=app.ROOT, env=app.brush_runtime_environment(os.environ.copy()))
    before = PlyData.read(source)['vertex'].data
    after = PlyData.read(export / 'probe.ply')['vertex'].data
    if len(before) != len(after):
        raise RuntimeError('Frozen probe changed the Gaussian count')
    drift = {name: float(np.max(np.abs(before[name] - after[name])))
             for name in before.dtype.names if name in after.dtype.names}
    result = {'model': model, 'parent_run': info['run_id'], 'command': command,
              'probe': 'One GPU step; learning rates 1e-20; noise and growth disabled.',
              'limits': 'Brush GPU render of the published PLY, not a browser screenshot.',
              'max_parameter_drift': drift,
              'published_file_unchanged': original_hash == hashlib.sha256(source.read_bytes()).hexdigest(),
              'published_metrics': app.evaluate_brush_renders(dataset, export, 1),
              'raw_checkpoint_metrics': app.evaluate_brush_renders(
                  original_dataset, parent_run / 'brush_export', info['quality']['training_steps'])}
    app.write_json(probe / 'result.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.model)), flush=True)
