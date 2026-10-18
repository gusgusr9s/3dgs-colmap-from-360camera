"""Conservative, opt-in panorama selection before COLMAP feature extraction.

Only discard a frame when appearance AND tracked displacement are small.
These are image-space redundancy measures, not estimated camera poses.
"""
import json
from pathlib import Path

import cv2
import numpy as np

MANIFEST = 'keyframe_selection.json'


def thumbnail(path):
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f'Cannot read panorama: {path}')
    image = cv2.resize(image, (512, 256), interpolation=cv2.INTER_AREA)
    # Exclude the rig/floor and the polar region; match the pipeline floor mask.
    return image[32:184].copy()


def redundant(reference, candidate):
    difference = cv2.absdiff(reference, candidate)
    tiles = np.array_split(difference, 8, axis=1)
    if any(float(tile.mean()) > 4 or float(np.mean(tile > 16)) > .04 for tile in tiles):
        return False
    points = cv2.goodFeaturesToTrack(reference, maxCorners=240, qualityLevel=.01, minDistance=8)
    if points is None or len(points) < 32:
        return False  # Textureless/uncertain images must not silently disappear.
    moved, status, _ = cv2.calcOpticalFlowPyrLK(reference, candidate, points, None)
    if moved is None or status is None:
        return False
    back, reverse, _ = cv2.calcOpticalFlowPyrLK(candidate, reference, moved, None)
    if back is None or reverse is None:
        return False
    valid = (status.ravel() == 1) & (reverse.ravel() == 1)
    valid &= np.linalg.norm(back[:, 0]-points[:, 0], axis=1) < .75
    valid &= np.isfinite(moved[:, 0]).all(axis=1)
    if valid.sum() < max(32, len(points)*.8):
        return False
    # Require observations across the panorama, rather than only one object.
    sectors = (points[valid, 0, 0] // 128).astype(int)
    if any(np.sum(sectors == sector) < 4 for sector in range(4)):
        return False
    displacement = np.linalg.norm(moved[valid, 0]-points[valid, 0], axis=1)
    return bool(np.median(displacement) < .75 and np.percentile(displacement, 90) < 1.5)


def select(directory, names, *, max_gap=3, loader=thumbnail, compare=redundant):
    """Keep endpoints, at most two skipped samples, and the frame before changes."""
    names = sorted(set(names))
    if max_gap < 1:
        raise ValueError('max_gap must be positive')
    if len(names) < 12:
        return names, {'short_sequence': len(names)}
    kept, reasons = {0, len(names)-1}, {'change_or_uncertain': 0, 'gap_limit': 0}
    anchor = loader(Path(directory)/names[0])
    anchor_index = 0
    for index in range(1, len(names)):
        candidate = loader(Path(directory)/names[index])
        duplicate = compare(anchor, candidate)
        if not duplicate or index-anchor_index >= max_gap:
            kept.add(index)
            if not duplicate:
                kept.add(index-1)  # Retain a bridge immediately before a turn/change.
                reasons['change_or_uncertain'] += 1
            else:
                reasons['gap_limit'] += 1
            anchor, anchor_index = candidate, index
    return [names[index] for index in sorted(kept)], reasons


def prepare(directory, database_directory, metadata, environment):
    mode = environment.get('COLMAP_KEYFRAMES', 'off').strip().lower()
    if mode not in {'off', 'adaptive'}:
        raise ValueError('COLMAP_KEYFRAMES must be off or adaptive')
    if mode == 'off':
        return [], None
    frames = metadata.get('frames')
    if not frames or any(not isinstance(frame, dict) or frame.get('frame_index') is None for frame in frames):
        raise ValueError('Adaptive keyframes requires Views metadata with frame_index values')
    names = sorted({f'pano_{int(frame["frame_index"]):06d}.jpg' for frame in frames})
    if len(names) < 3:
        raise ValueError('Adaptive keyframes requires at least three panorama frames')
    selected, reasons = select(directory, names)
    root = Path(database_directory)
    report = dict(schema=1, mode=mode, source_names=names, selected_names=selected,
                  original_count=len(names), selected_count=len(selected), max_sample_gap=3,
                  reasons=reasons, scope='Only selected registered panoramas contribute training views; no pose interpolation.')
    (root/MANIFEST).write_text(json.dumps(report, indent=2), encoding='utf8')
    listing = root/'keyframes.txt'
    listing.write_text('\n'.join(selected)+'\n', encoding='utf8')
    return ['--image_list_path', str(listing)], report


def recovery_names(database_directory, expected):
    """Validate the persisted selection; never recompute it on resume."""
    path = Path(database_directory)/MANIFEST
    if not path.exists():
        return expected
    report = json.loads(path.read_text(encoding='utf8'))
    selected = report.get('selected_names', [])
    if (report.get('schema') != 1 or report.get('mode') != 'adaptive'
            or set(report.get('source_names', [])) != expected
            or len(selected) != len(set(selected)) or len(selected) < 3
            or not set(selected).issubset(expected)):
        raise ValueError('Invalid adaptive keyframe manifest; cannot resume this database')
    return set(selected)


def main():
    import argparse
    import time
    parser = argparse.ArgumentParser(description='Read-only adaptive keyframe preview; does not start COLMAP or edit images.')
    parser.add_argument('panoramas', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    names = sorted(path.name for path in args.panoramas.glob('pano_*.jpg'))
    if len(names) < 3:
        parser.error('Expected at least three pano_*.jpg files')
    started = time.monotonic()
    selected, reasons = select(args.panoramas, names)
    report = dict(original_count=len(names), selected_count=len(selected), selected_names=selected,
                  seconds=round(time.monotonic()-started, 3), reasons=reasons,
                  note='Selection preview only; pose coverage and reconstruction quality are not verified.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps({key:value for key,value in report.items() if key != 'selected_names'}))


if __name__ == '__main__':
    main()
