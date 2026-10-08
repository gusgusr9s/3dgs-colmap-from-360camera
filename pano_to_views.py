#!/usr/bin/env python3
"""Convert an equirectangular 360 image/video into perspective views."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def perspective_map(width: int, height: int, fov_deg: float, yaw_deg: float, pitch_deg: float):
    f = 0.5 * width / math.tan(math.radians(fov_deg) / 2.0)
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    xx = (x - (width - 1) / 2.0) / f
    yy = (y - (height - 1) / 2.0) / f
    rays = np.stack([xx, -yy, np.ones_like(xx)], axis=-1)
    rays /= np.linalg.norm(rays, axis=-1, keepdims=True)

    yaw, pitch = map(math.radians, (yaw_deg, pitch_deg))
    ry = np.array([[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0],
                   [-math.sin(yaw), 0, math.cos(yaw)]], dtype=np.float32)
    rx = np.array([[1, 0, 0], [0, math.cos(pitch), -math.sin(pitch)],
                   [0, math.sin(pitch), math.cos(pitch)]], dtype=np.float32)
    rays = rays @ (ry @ rx).T

    lon = np.arctan2(rays[..., 0], rays[..., 2])
    lat = np.arcsin(np.clip(rays[..., 1], -1, 1))
    map_x = ((lon / (2 * np.pi)) + 0.5) * (width - 1)
    map_y = (0.5 - lat / np.pi) * (height - 1)
    return map_x.astype(np.float32), map_y.astype(np.float32), f


def frames_from_input(path: Path, sample_every: int, start_frame=0, end_frame=None):
    """Sample relative to start_frame; end_frame is exclusive, IDs stay original."""
    if sample_every < 1 or start_frame < 0 or (end_frame is not None and end_frame <= start_frame):
        raise ValueError('Invalid sampling range')
    if path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f'Cannot read image: {path}')
        yield 0, image
        return

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open video: {path}')
    try:
        if start_frame:
            if not cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame):
                raise RuntimeError('Cannot seek to the selected start frame')
        index = start_frame
        while end_frame is None or index < end_frame:
            # grab skips conversion for unsampled frames, while preserving decoding order.
            if not cap.grab():
                break
            if (index - start_frame) % sample_every == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    raise RuntimeError(f'Cannot decode frame {index}')
                yield index, frame
            index += 1
    finally:
        cap.release()


def project_frame(image, width, height, fov, yaw=0, pitch=0, panorama=True, maps=None):
    """Shared projection for previews and generated training images."""
    if not panorama:
        return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    mx, my = maps if maps is not None else perspective_map(width, height, fov, yaw, pitch)[:2]
    return cv2.remap(image, mx * (image.shape[1] - 1) / (width - 1),
                     my * (image.shape[0] - 1) / (height - 1),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def panorama_angles(views: int, quality: str):
    angles = [(float(yaw), 0.0) for yaw in np.linspace(0, 360, views, endpoint=False)]
    if quality in {"high", "ultra"}:
        vertical_views = max(4, int(math.ceil(views / 2)))
        for pitch in (-55.0, 55.0):
            angles.extend(
                (float(yaw), pitch)
                for yaw in np.linspace(0, 360, vertical_views, endpoint=False)
            )
    return angles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--width', type=int, default=640)
    ap.add_argument('--height', type=int, default=640)
    ap.add_argument('--fov', type=float, default=90)
    ap.add_argument('--views', type=int, default=8)
    ap.add_argument('--sample-every', type=int, default=10)
    ap.add_argument('--start-seconds', type=float, default=0)
    ap.add_argument('--end-seconds', type=float, default=0, help='0 means end of video')
    ap.add_argument('--projection', choices=('auto', 'panorama', 'walkthrough'), default='auto',
                    help='auto detects 2:1 panoramas; walkthrough keeps each video frame as one camera view')
    ap.add_argument('--quality', choices=('preview', 'high', 'ultra'), default='high',
                    help='high/ultra add upward and downward view rings')
    args = ap.parse_args()
    from capture_preview import sampling_plan
    from view_suggestions import probe_source
    plan = sampling_plan(probe_source(args.input), vars(args))
    args.output.mkdir(parents=True, exist_ok=True)

    first = True
    frames = []
    source_mode = None
    for frame_index, pano in frames_from_input(args.input, plan['effective_sample_every'],
                                              plan['start_frame'], plan['end_frame']):
        is_panorama = args.projection == 'panorama' or (
            args.projection == 'auto' and abs(pano.shape[1] / pano.shape[0] - 2.0) < 0.08
        )
        source_mode = 'panorama' if is_panorama else 'walkthrough_video'
        if first:
            map_x, map_y, focal = perspective_map(args.width, args.height, args.fov, 0, 0)
            first = False
        angles = panorama_angles(args.views, args.quality) if is_panorama else [(0.0, 0.0)]
        for view_index, (yaw, pitch) in enumerate(angles):
            view = project_frame(pano, args.width, args.height, args.fov, yaw, pitch, is_panorama)
            name = f'frame_{frame_index:06d}_view_{view_index:02d}.jpg'
            if not cv2.imwrite(str(args.output / name), view, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                raise RuntimeError(f'Cannot write generated view: {name}')
            frames.append({'file_path': name, 'transform_matrix': np.eye(4).tolist(),
                           'yaw_deg': yaw, 'pitch_deg': pitch, 'frame_index': frame_index})

    if not frames:
        raise ValueError('No readable frames in the selected clip')
    meta = {'camera_model': 'PINHOLE', 'fl_x': focal, 'fl_y': focal,
            'cx': (args.width - 1) / 2, 'cy': (args.height - 1) / 2,
            'w': args.width, 'h': args.height, 'frames': frames,
            'source_mode': source_mode,
            'source_name': args.input.name, 'capture_plan': plan,
            'settings': {'width': args.width, 'height': args.height, 'fov': args.fov,
                         'views': args.views, 'quality': args.quality, 'projection': args.projection,
                         'sample_every': plan['effective_sample_every'],
                         'requested_sample_every': args.sample_every,
                         'start_seconds': args.start_seconds, 'end_seconds': args.end_seconds},
            'warning': 'Placeholder poses: use COLMAP/SLAM poses for real 3D reconstruction.'}
    (args.output / 'transforms.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    print(f'Wrote {len(frames)} perspective views to {args.output}')
    print(f'Wrote {args.output / "transforms.json"}')


if __name__ == '__main__':
    main()
