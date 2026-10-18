"""Controlled source-resolution comparison; preserve the verified 6K baseline.

Poses, seed (including its original colors), masks, path, held-out split and
training profile remain fixed. This measures appearance resolution, not the
ability of SfM to recover poses from lower-resolution footage.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
from app.web import server as w
from app.pipeline.reconstruction_preview import build_pose_preview

BASELINE = '2e88b6a8dc'
REPORT = w.EXPERIMENTS / 'concourse_resolution_comparison.json'


def prepare_source(width):
    raw = w.RAW / '360loc_concourse_walk_stride2'
    records = w.read_json(raw / 'frames.json', [])
    name = f'360loc_concourse_comparison_{width}'
    frames_dir = w.RAW / name
    target = w.RAW / (name + '.mkv')
    if target.exists():
        raise ValueError(f'Comparison source already exists: {target}; resume the recorded job instead')
    frames_dir.mkdir(exist_ok=False)
    for record in records:
        image = cv2.imread(str(raw / 'images' / record['filename']))
        if image is None or image.shape[:2] != (3072, 6144):
            raise ValueError('Original 6K image missing')
        image = cv2.resize(image, (width, width // 2), interpolation=cv2.INTER_AREA)
        if not cv2.imwrite(str(frames_dir / record['filename']), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError('Cannot save resized panorama')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                    '-framerate', '2', '-i', str(frames_dir / 'frame_%06d.jpg'),
                    '-c:v', 'copy', str(target)], check=True)
    profile = w.probe_source(target)
    if (profile['width'], profile['height'], profile['frame_count']) != (width, width//2, len(records)):
        raise ValueError('Derived video dimensions/count differ')
    w.write_json(target.with_suffix('.source.json'), {
        'description': 'Controlled lower-resolution derivative of the verified 360Loc walking loop',
        'parent': '360loc_concourse_walk_stride2.mkv', 'source_url': 'https://github.com/HuajianUP/360Loc',
        'resolution': [width, width//2], 'frames': len(records), 'resize': 'INTER_AREA, JPEG quality 95',
        'fps': 2, 'fps_note': 'Assigned playback rate for still panoramas, not original capture FPS',
        'comparison_limitation': 'Reuses 6K poses, depth seed/colors and resized masks; isolates image resolution',
    })
    return target


def prepare_dataset(job):
    directory = w.JOBS / job
    target = directory / 'brush_data'
    base = w.JOBS / BASELINE / 'brush_data'
    parent = w.read_json(base / 'transforms.json')
    current = w.read_json(directory / 'transforms.json')
    by_name = {Path(f['file_path']).name: f for f in parent['frames']}
    if set(by_name) != {Path(f['file_path']).name for f in current['frames']}:
        raise ValueError('Comparison must retain every baseline frame and direction')
    (target / 'images').mkdir(parents=True)
    (target / 'masks').mkdir()
    frames = []
    for frame in current['frames']:
        name = Path(frame['file_path']).name
        old = by_name[name]
        if any(old[k] != frame[k] for k in ('frame_index','yaw_deg','pitch_deg')):
            raise ValueError('Pose/frame mismatch')
        os.link(directory / name, target / 'images' / name)
        mask = cv2.imread(str(base / 'masks' / (Path(name).stem + '.png')), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise ValueError('Baseline mask missing')
        mask = cv2.resize(mask, (current['w'], current['h']), interpolation=cv2.INTER_NEAREST)
        if not cv2.imwrite(str(target / 'masks' / (Path(name).stem + '.png')), mask):
            raise OSError('Cannot save comparison mask')
        frames.append({**frame, 'file_path': 'images/' + name, 'transform_matrix': old['transform_matrix']})
    shutil.copy2(base / 'sparse_pc.ply', target / 'sparse_pc.ply')
    dataset = {**current, 'frames': frames, 'ply_file_path': 'sparse_pc.ply',
               'world_units': 'meters', 'world_up': [0,0,1],
               'pose_source': 'Fixed baseline 360Loc published poses (resolution comparison)',
               'warning': 'Controlled comparison: reused 6K depth seed/colors and resized masks'}
    w.write_json(target / 'transforms.json', dataset)
    quality = {**w.read_json(w.JOBS / BASELINE / 'pose_status.json')['quality'],
               'pose_source': dataset['pose_source'], 'comparison_parent_job': BASELINE,
               'comparison_limitation': dataset['warning']}
    preview = build_pose_preview(directory, quality)
    w.write_json(directory / 'pose_preview.json', preview)
    w.write_json(directory / 'pose_status.json', {
        'state': 'ready', 'stage': 'poses', 'progress': 100, 'quality': quality,
        'step': 'Fixed baseline poses and resized masks ready',
        'summary': preview['summary'], 'warnings': preview['warnings'],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--widths', nargs='+', type=int, default=[1920, 3840])
    args = parser.parse_args()
    if any(width not in (1920, 3840) for width in args.widths):
        parser.error('Supported comparison widths: 1920, 3840')
    if REPORT.exists():
        raise ValueError('Report already exists; preserve completed/active experiments')
    baseline_model = w.model_file_for_job(BASELINE)
    report = {'batch': REPORT.stem, 'started_at': w.utc_now(), 'baseline': baseline_model.name,
              'purpose': __doc__, 'cases': []}
    w.write_json(REPORT, report)
    for width in args.widths:
        record = {'name': f'Concourse {width}x{width//2} - fixed full walking loop', 'state': 'PREPARING', 'source_width': width}
        report['cases'].append(record); w.write_json(REPORT, report)
        try:
            source = prepare_source(width)
            job = uuid.uuid4().hex[:10]
            directory = w.JOBS / job
            directory.mkdir()
            side = max(160, int((width * .25) // 64) * 64)
            settings = {**w.DEFAULT_VIEW_SETTINGS, 'width': side, 'height': side,
                        'quality': 'ultra', 'projection': 'panorama', 'sample_every': 1}
            w.write_json(directory / 'job.json', {'label': record['name'], 'source_key': w.source_key_for_path(source),
                                                 'source_name': source.name, 'settings': settings})
            record.update(job=job, source_key=w.source_key_for_path(source), settings=settings)
            w.write_json(REPORT, report)
            if not w.claim_task(job): raise RuntimeError('Job busy')
            w.generate_views(job, w.source_key_for_path(source), settings)
            if w.views_status(job).get('state') != 'ready': raise RuntimeError('View generation failed')
            if not w.claim_task(job): raise RuntimeError('Job busy')
            try:
                prepare_dataset(job)
                identifier = w.allocate_model_run(job)
                record.update(state='TRAINING', run_id=identifier); w.write_json(REPORT, report)
                print(f'TRAINING {job} {identifier} source={width} crop={side}', flush=True)
                w.generate_3d(job, identifier=identifier)
            finally:
                w.release_task(job)
            status = w.read_json(directory / 'model_runs' / identifier / 'status.json')
            if status.get('state') != 'done': raise RuntimeError(status.get('error', 'Training did not finish'))
            record.update(state='DONE', model=status['model'], quality=status['quality'], finished_at=w.utc_now())
            print('DONE ' + json.dumps(record), flush=True)
        except Exception as exc:
            record.update(state='ERROR', error=str(exc)); w.write_json(REPORT, report)
            raise
        w.write_json(REPORT, report)
    report['finished_at'] = w.utc_now(); w.write_json(REPORT, report)


if __name__ == '__main__':
    main()
