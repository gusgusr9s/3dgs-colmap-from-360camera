"""Bounded camera depth samples and track overlap from a native sparse model."""
from pathlib import Path

import numpy as np


def measure(reconstruction):
    images = []
    for image in reconstruction.images.values():
        name = Path(image.name).stem
        if name.startswith('pano_') and name[5:].isdigit():
            images.append((int(name[5:]), image))
    images.sort(key=lambda item: item[0])
    depths, tracks = {}, []
    for frame, image in images:
        ids = sorted({int(point.point3D_id) for point in image.get_observation_points2D()})
        tracks.append(set(ids))
        sample = ids[::max(1, int(np.ceil(len(ids)/256)))][:256]
        if sample:
            xyz = np.asarray([reconstruction.points3D[key].xyz for key in sample])
            distance = np.linalg.norm(xyz-image.projection_center(), axis=1)
            distance = distance[np.isfinite(distance) & (distance > 1e-12)]
            if len(distance):
                depths[str(frame)] = float(np.median(distance))
    overlap = {}
    for stride in range(1, 9):
        pairs = [(tracks[i], tracks[i+stride]) for i in range(len(tracks)-stride)]
        if pairs:
            common = [len(a & b) for a, b in pairs]
            fractions = [n/max(1, min(len(a),len(b))) for n,(a,b) in zip(common,pairs)]
            overlap[str(stride)] = dict(p10_common=float(np.percentile(common,10)),
                                        p10_fraction=float(np.percentile(fractions,10)))
    return dict(version=1, depths=depths, overlap=overlap)
