"""Prepare this downloaded 360Loc Concourse loop using publisher poses/depth.

Keeps all generated perspective cameras. Original depth PNGs are fetched with
ZIP CRC checks; depth scale is checked against metric poses in overlapping views.
The output is a normal Brush dataset and the app's reviewable pose cache.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys
import zipfile

import cv2
import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from download_walking_sample import ARCHIVE, read_entry
from download_omniphotos import RemoteZip
from reconstruction_preview import build_pose_preview
from spherical_to_brush import crop_rotation, create_dynamic_person_masks


def depth_points(depth, stride=24):
    """Publisher sphereRay convention: camera +Y down, +Z forward."""
    height, width = depth.shape
    u, v = np.meshgrid(np.arange(0, width, stride),
                       np.arange(0, int(height * .72), stride))
    lon, lat = u * 2 * np.pi / width - np.pi, np.pi / 2 - v * np.pi / height
    rays = np.stack([np.cos(lat) * np.sin(lon), -np.sin(lat),
                     np.cos(lat) * np.cos(lon)], -1)
    values = depth[v, u].astype(float)
    valid = (values > 30) & (values < 5000)
    return rays[valid], values[valid], u[valid], v[valid]


def check_depth_scale(depth_a, depth_b, pose_a, pose_b):
    rays, values, _, _ = depth_points(depth_a, 24)
    scores = {}
    height, width = depth_b.shape
    for scale in (.002, .005, .01, .02, .05, .1):
        world = (rays * (values * scale)[:, None]) @ pose_a[:3, :3].T + pose_a[:3, 3]
        local = (world - pose_b[:3, 3]) @ pose_b[:3, :3]
        distance = np.linalg.norm(local, axis=1)
        lon = np.arctan2(local[:, 0], local[:, 2])
        lat = np.arcsin(np.clip(-local[:, 1] / distance, -1, 1))
        u = np.rint((lon / (2*np.pi) + .5) * width).astype(int) % width
        v = np.clip(np.rint((.5 - lat / np.pi) * height).astype(int), 0, height-1)
        reference = depth_b[v, u] * scale
        valid = reference > .3
        error = np.abs(np.log(distance[valid] / reference[valid]))
        scores[str(scale)] = {'median_abs_log_error': float(np.median(error)),
                              'within_3_percent': float(np.mean(error < .03))}
    best = min(scores, key=lambda s: scores[s]['median_abs_log_error'])
    if float(best) != .01 or scores['0.01']['within_3_percent'] < .3:
        raise ValueError('Published depth does not align with metric poses: ' + str(scores))
    return scores


def filter_seed_with_masks(xyz, frames, dataset, directory):
    supported = np.zeros(len(xyz), dtype=bool)
    for frame in frames:
        pose = np.asarray(frame['transform_matrix'])
        local = (xyz - pose[:3, 3]) @ pose[:3, :3]
        depth = -local[:, 2]
        px = dataset['fl_x'] * local[:, 0] / np.maximum(depth, 1e-9) + dataset['cx']
        py = -dataset['fl_y'] * local[:, 1] / np.maximum(depth, 1e-9) + dataset['cy']
        ids = np.flatnonzero((depth > .1) & (px >= 0) & (py >= 0)
                             & (px < dataset['w']) & (py < dataset['h']))
        mask = cv2.imread(str(directory / 'masks' / (Path(frame['file_path']).stem + '.png')), 0)
        if mask is None:
            raise ValueError('Missing person mask')
        good = mask[py[ids].astype(int), px[ids].astype(int)] > 127
        supported[ids[good]] = True
    return supported


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    parser.add_argument('--depth-every', type=int, default=8)
    args = parser.parse_args()
    if Path(args.job).name != args.job or args.depth_every < 1:
        parser.error('Invalid job/depth interval')
    return args


def prepare(args):
    from web_app import write_json
    job = ROOT / 'web_data' / 'jobs' / args.job
    dataset_dir = job / 'brush_data'
    if dataset_dir.exists():
        raise ValueError('Prepared dataset already exists; preserve it and use another job')
    raw = ROOT / 'data' / 'raw' / '360loc_concourse_walk_stride2'
    metadata = json.loads((job / 'transforms.json').read_text(encoding='utf-8'))
    records = json.loads((raw / 'frames.json').read_text(encoding='utf-8'))
    manifest = json.loads((job / 'job.json').read_text(encoding='utf-8'))
    if manifest['source_key'] != 'data/raw:360loc_concourse_walk_stride2.mkv':
        raise ValueError('This importer only supports the CRC-verified Concourse source')
    if {f['frame_index'] for f in metadata['frames']} != set(range(len(records))):
        raise ValueError('Expected every downloaded camera position')
    args.started_preparing = True
    write_json(job / 'pose_status.json', {'state': 'running', 'stage': 'poses', 'progress': 5,
                                        'step': 'Validating published 360Loc poses and depth'})
    cache = raw / 'depth'
    cache.mkdir(exist_ok=True)
    selected = sorted(set(range(0, len(records), args.depth_every)) | {len(records)-1, 0, 8, 80, 88, 160, 168})
    with RemoteZip(ARCHIVE) as remote, zipfile.ZipFile(remote) as archive:
        entries = archive.infolist()
        offsets = sorted([e.header_offset for e in entries] + [archive.start_dir])
        ends = dict(zip(offsets, offsets[1:]))
        size = remote.size
        pose_entry = archive.getinfo('concourse/mapping/daytime_360_1/camera_pose.json')
        poses_data = read_entry(pose_entry, ends[pose_entry.header_offset], size)
        entries_by_name = {e.filename: e for e in entries}
    poses_json = json.loads(poses_data)
    (raw / 'camera_pose.json').write_bytes(poses_data)
    poses = []
    for record in records:
        pose = np.asarray(poses_json[Path(record['archive_entry']).name], dtype=float)
        qw, qx, qy, qz = record['quaternion_wxyz']
        if (pose.shape != (4,4) or not np.isfinite(pose).all()
            or not np.allclose(pose[:3, 3], record['position_m'], atol=1e-8)
            or not np.allclose(pose[:3, :3], Rotation.from_quat([qx,qy,qz,qw]).as_matrix(), atol=1e-8)):
            raise ValueError('Pose JSON/TXT mismatch')
        poses.append(pose)
    print(f'Validated {len(poses)} published camera poses.', flush=True)

    def download(index):
        name = Path(records[index]['archive_entry']).stem + '.png'
        entry = entries_by_name['concourse/mapping/daytime_360_1/depth/' + name]
        target = cache / name
        import binascii
        if not target.is_file() or (binascii.crc32(target.read_bytes()) & 0xffffffff) != entry.CRC:
            data = read_entry(entry, ends[entry.header_offset], size)
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(target)
        depth = cv2.imread(str(target), cv2.IMREAD_UNCHANGED)
        if depth is None or depth.shape != (3072,6144) or depth.dtype != np.uint16:
            raise ValueError('Unexpected original depth format')
        return index
    with ThreadPoolExecutor(max_workers=6) as pool:
        for done, future in enumerate(as_completed([pool.submit(download, i) for i in selected]), 1):
            print(f'Depth CRC verified {done}/{len(selected)} (camera {future.result()}).', flush=True)
    def load_depth(index):
        return cv2.imread(str(cache / (Path(records[index]['archive_entry']).stem + '.png')), cv2.IMREAD_UNCHANGED)
    validations = []
    for a,b in ((0,8), (80,88), (160,168)):
        scores = check_depth_scale(load_depth(a), load_depth(b), poses[a], poses[b])
        validations.append({'cameras': [a,b], 'baseline_m': float(np.linalg.norm(poses[a][:3,3]-poses[b][:3,3])), 'scores': scores})
    print('Depth scale 0.01 m verified on three separated overlapping pairs.', flush=True)
    images = dataset_dir / 'images'
    images.mkdir(parents=True)
    frames = []
    for frame in metadata['frames']:
        pose = poses[frame['frame_index']].copy()
        pose[:3,:3] = pose[:3,:3] @ crop_rotation(frame['yaw_deg'], frame['pitch_deg'])
        pose[:3,1:3] *= -1
        source = (job / frame['file_path']).resolve()
        if source.parent != job.resolve() or not source.is_file():
            raise ValueError('Invalid perspective path')
        os.link(source, images / source.name)
        frames.append({**frame, 'file_path': 'images/' + source.name, 'transform_matrix': pose.tolist()})
    dataset = {**metadata, 'frames': frames, 'ply_file_path': 'sparse_pc.ply',
               'pose_source': '360Loc published camera_pose.json, cross-checked against pose TXT',
               'world_units': 'meters', 'world_up': [0,0,1]}
    (dataset_dir / 'transforms.json').write_text(json.dumps(dataset, indent=2), encoding='utf-8')
    import torch
    torch.set_num_threads(8)
    torch.set_num_interop_threads(2)
    print(f'Masking people/operator in {len(frames)} perspective images (CPU, 8 threads).', flush=True)
    write_json(job / 'pose_status.json', {'state': 'running', 'stage': 'poses', 'progress': 39,
                                        'step': f'Masking people in {len(frames)} perspective images'})
    masks = create_dynamic_person_masks(dataset_dir)
    print('Person masks complete: ' + json.dumps(masks), flush=True)
    chunks, colors = [], []
    for index in selected:
        rays, values, u, v = depth_points(load_depth(index))
        xyz = (rays * (values * .01)[:,None]) @ poses[index][:3,:3].T + poses[index][:3,3]
        image = cv2.imread(str(raw / 'images' / records[index]['filename']))
        rgb = image[v,u,::-1]
        valid = filter_seed_with_masks(xyz, [f for f in frames if f['frame_index'] == index], dataset, dataset_dir)
        chunks.append(xyz[valid]); colors.append(rgb[valid])
        print(f'Seed camera {index}: {int(valid.sum())} masked depth points.', flush=True)
    xyz, rgb = np.concatenate(chunks), np.concatenate(colors)
    _, indices = np.unique(np.floor(xyz / .05).astype(np.int32), axis=0, return_index=True)
    xyz, rgb = xyz[indices], rgb[indices]
    vertices = np.empty(len(xyz), dtype=[(k,'<f4') for k in ('x','y','z')] + [(k,'u1') for k in ('red','green','blue')])
    for i,k in enumerate(('x','y','z')): vertices[k] = xyz[:,i]
    for i,k in enumerate(('red','green','blue')): vertices[k] = rgb[:,i]
    PlyData([PlyElement.describe(vertices, 'vertex')], text=False).write(dataset_dir / 'sparse_pc.ply')
    centers = np.asarray([p[:3,3] for p in poses])
    quality = {**masks, 'pose_source': dataset['pose_source'], 'world_units': 'meters',
               'registered_panoramas': len(poses), 'input_panoramas': len(poses),
               'perspective_views': len(frames), 'sparse_points': len(xyz),
               'scene_radius_p90': float(np.percentile(np.linalg.norm(xyz-np.median(centers,axis=0),axis=1),90)),
               'max_same_frame_center_spread': 0., 'path_length_m': float(np.linalg.norm(np.diff(centers,axis=0),axis=1).sum()),
               'loop_closure_m': float(np.linalg.norm(centers[0]-centers[-1])),
               'seed_source': f'CRC-verified published depth, sampled every {args.depth_every} positions, person-masked, 5cm voxels',
               'depth_meters_per_unit': .01, 'depth_scale_validation': validations}
    preview = build_pose_preview(job, quality)
    preview['coordinate_system'] = 'camera-to-world, +Y up, -Z forward; world Z up; meters'
    write_json(job / 'pose_preview.json', preview)
    status = {'state': 'ready', 'stage': 'poses', 'progress': 100, 'step': 'Published poses and masked depth ready',
              'quality': quality, 'summary': preview['summary'], 'warnings': preview['warnings']}
    write_json(job / 'pose_status.json', status)
    write_json(job / '360loc_import.json', quality)
    print('READY: ' + json.dumps(preview['summary']), flush=True)


if __name__ == '__main__':
    from web_app import claim_task, release_task
    args = arguments()
    if not claim_task(args.job):
        raise RuntimeError('Job is busy or missing')
    try:
        prepare(args)
    except Exception as exc:
        from web_app import write_json
        if getattr(args, 'started_preparing', False):
            write_json(ROOT / 'web_data' / 'jobs' / args.job / 'pose_status.json',
                       {'state': 'error', 'stage': 'poses', 'progress': 0, 'error': str(exc)})
        raise
    finally:
        release_task(args.job)
