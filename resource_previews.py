"""Small cached previews derived from local images, videos, and PLY geometry."""
import hashlib
import uuid

import cv2
import numpy as np
from plyfile import PlyData, PlyParseError

SIZE = (384, 216)


def placeholder():
    image = np.full((SIZE[1], SIZE[0], 3), (29, 20, 13), np.uint8)
    cv2.rectangle(image, (151, 65), (233, 133), (105, 88, 70), 2)
    cv2.circle(image, (172, 84), 6, (151, 124, 95), -1)
    cv2.polylines(image, [np.array([[153, 126], [180, 102], [194, 115], [209, 98], [231, 125]])], False, (151, 124, 95), 2)
    cv2.putText(image, 'Preview unavailable', (99, 164), cv2.FONT_HERSHEY_SIMPLEX, .5, (172, 155, 139), 1, cv2.LINE_AA)
    return image


def fit_image(image, size=SIZE):
    width, height = size
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(image, (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    result = np.full((height, width, 3), (19, 11, 7), np.uint8)
    x, y = (width - resized.shape[1]) // 2, (height - resized.shape[0]) // 2
    result[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return result


def read_image(path):
    # imdecode supports Unicode filenames on Windows.
    return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)


def video_image(path):
    capture = cv2.VideoCapture(str(path))
    try:
        count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = capture.get(cv2.CAP_PROP_FPS)
        if count > 1 and fps > 0:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(min(count * .1, fps * 2)))
        ok, image = capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, image = capture.read()
        return image if ok else None
    finally:
        capture.release()


def ply_image(path):
    """A bounded point overview, rather than an expensive Gaussian render."""
    vertices = PlyData.read(str(path), mmap='r')['vertex'].data
    if not len(vertices) or not {'x', 'y', 'z'}.issubset(vertices.dtype.names):
        return None
    sample = vertices[np.linspace(0, len(vertices) - 1, min(len(vertices), 40000), dtype=int)]
    points = np.column_stack([sample[name] for name in ('x', 'y', 'z')]).astype(np.float64)
    if {'f_dc_0', 'f_dc_1', 'f_dc_2'}.issubset(vertices.dtype.names):
        colors = (.5 + .28209479177387814 * np.column_stack([sample[name] for name in ('f_dc_0', 'f_dc_1', 'f_dc_2')])) * 255
    elif {'red', 'green', 'blue'}.issubset(vertices.dtype.names):
        colors = np.column_stack([sample[name] for name in ('red', 'green', 'blue')]).astype(float)
    else:
        colors = np.tile([169, 196, 240], (len(sample), 1))
    valid = np.isfinite(points).all(axis=1) & np.isfinite(colors).all(axis=1)
    if 'opacity' in vertices.dtype.names:
        valid &= np.isfinite(sample['opacity']) & (sample['opacity'] > -3)
    points, colors = points[valid], colors[valid]
    if not len(points):
        return None
    points /= max(float(np.max(np.abs(points))), 1e-12)
    points -= np.median(points, axis=0)
    # Principal axes give uploaded models of arbitrary scale a useful overview.
    _, axes = np.linalg.eigh(points.T @ points)
    projected = points @ axes[:, ::-1]
    low, high = np.percentile(projected[:, :2], [1, 99], axis=0)
    extent = np.maximum(high - low, .00001)
    scale = min((SIZE[0] - 24) / extent[0], (SIZE[1] - 24) / extent[1])
    pixels = (projected[:, :2] - (low + high) / 2) * scale
    pixels[:, 0] += SIZE[0] / 2
    pixels[:, 1] = SIZE[1] / 2 - pixels[:, 1]
    visible = (pixels[:, 0] >= 2) & (pixels[:, 0] < SIZE[0] - 2) & (pixels[:, 1] >= 2) & (pixels[:, 1] < SIZE[1] - 2)
    order = np.argsort(projected[:, 2][visible])
    pixels = pixels[visible][order].astype(int)
    colors = np.clip(colors[visible][order, ::-1], 0, 255).astype(np.uint8)
    image = np.full((SIZE[1], SIZE[0], 3), (19, 11, 7), np.uint8)
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        image[pixels[:, 1] + dy, pixels[:, 0] + dx] = colors
    return image


def thumbnail_file(paths, mode, cache_directory):
    signatures = [(str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns) for path in paths]
    digest = hashlib.sha256(repr(('v1', mode, signatures)).encode()).hexdigest()
    target = cache_directory / (digest + '.jpg')
    if target.is_file():
        return target
    image = None
    try:
        if paths and mode == 'ply':
            image = ply_image(paths[0])
        elif paths and mode == 'video':
            image = video_image(paths[0])
        elif paths and mode == 'mosaic':
            images = [read_image(path) for path in paths[:4]]
            images = [item for item in images if item is not None]
            if len(images) == 1:
                image = images[0]
            elif images:
                image = np.full((SIZE[1], SIZE[0], 3), (19, 11, 7), np.uint8)
                for index, item in enumerate(images):
                    x, y = index % 2 * (SIZE[0] // 2), index // 2 * (SIZE[1] // 2)
                    image[y:y + SIZE[1] // 2, x:x + SIZE[0] // 2] = fit_image(item, (SIZE[0] // 2, SIZE[1] // 2))
        elif paths:
            image = read_image(paths[0])
    except (OSError, ValueError, TypeError, IndexError, KeyError, OverflowError, np.linalg.LinAlgError, PlyParseError, cv2.error):
        image = None
    image = fit_image(image) if image is not None else placeholder()
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise ValueError('Could not encode preview')
    cache_directory.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(encoded.tobytes())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
