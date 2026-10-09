"""Download a real 6K 360Loc walking loop, keeping original JPEGs and poses.

Byte ranges extract only selected panorama entries from the large ZIP.
A JPEG-stream MKV makes the sequence selectable in the web app without
re-encoding. Its 2 fps playback is assigned here, not an original capture rate.
"""

import argparse
import binascii
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import urllib.request
import zipfile
import zlib

import cv2
import numpy as np

from download_omniphotos import RemoteZip


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = 'https://hkustvgd.com/statics/360loc/concourse.zip'
PREFIX = 'concourse/mapping/daytime_360_1/image/'
POSE_ENTRY = 'concourse/pose/360_mapping_gt.txt'
REFERENCE = 'https://github.com/HuajianUP/360Loc'


def read_entry(info, end_offset, archive_size):
    """Read one complete local ZIP member in one verified HTTP range."""
    start, end = info.header_offset, end_offset - 1
    for attempt in range(3):
        try:
            request = urllib.request.Request(ARCHIVE, headers={
                'Range': f'bytes={start}-{end}', 'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(request, timeout=90) as response:
                expected = f'bytes {start}-{end}/{archive_size}'
                if response.status != 206 or response.headers.get('Content-Range') != expected:
                    raise ValueError('Server returned an incorrect ZIP byte range')
                data = response.read()
            if len(data) != end - start + 1:
                raise OSError('Incomplete ZIP member download')
            header = struct.unpack('<4s5H3I2H', data[:30])
            if header[0] != b'PK\x03\x04' or header[2] & 1:
                raise ValueError('Invalid or encrypted local ZIP member')
            offset = 30 + header[-2] + header[-1]
            compressed = data[offset:offset + info.compress_size]
            if info.compress_type == zipfile.ZIP_DEFLATED:
                result = zlib.decompress(compressed, -15)
            elif info.compress_type == zipfile.ZIP_STORED:
                result = compressed
            else:
                raise ValueError('Unsupported ZIP compression method')
            if len(result) != info.file_size or binascii.crc32(result) & 0xffffffff != info.CRC:
                raise ValueError('ZIP size or CRC check failed')
            return result
        except (OSError, ValueError, zlib.error, struct.error):
            if attempt == 2:
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stride', type=int, default=2,
                        help='Keep every Nth position; 1 downloads all 491 originals')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.stride <= 20 or not 1 <= args.workers <= 8:
        parser.error('Use stride 1..20 and workers 1..8')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg is required to package the JPEG sequence')
    stem = f'360loc_concourse_walk_stride{args.stride}'
    root = ROOT / 'data' / 'raw'
    directory = root / stem
    images = directory / 'images'
    images.mkdir(parents=True, exist_ok=True)
    with RemoteZip(ARCHIVE) as remote, zipfile.ZipFile(remote) as archive:
        entries = archive.infolist()
        image_entries = sorted((item for item in entries
                                if item.filename.startswith(PREFIX)
                                and item.filename.endswith('.jpg')
                                and '/' not in item.filename[len(PREFIX):]),
                               key=lambda item: item.filename)
        if not image_entries:
            raise ValueError('No mapping panoramas in the published archive')
        poses = archive.read(POSE_ENTRY).decode('utf-8')
        archive_size = remote.size
        offsets = sorted([item.header_offset for item in entries] + [archive.start_dir])
    (directory / 'published_poses.txt').write_text(poses, encoding='utf-8')
    pose_rows = {parts[0]: [float(value) for value in parts[1:]]
                 for line in poses.splitlines() if (parts := line.split())}
    selected = image_entries[::args.stride]
    if selected[-1].filename != image_entries[-1].filename:
        selected.append(image_entries[-1])
    records = []
    for ordinal, info in enumerate(selected):
        pose_key = info.filename.removeprefix('concourse/')
        pose = pose_rows.get(pose_key)
        if pose is None or len(pose) != 7 or not np.isfinite(pose).all():
            raise ValueError('Missing or invalid published pose: ' + pose_key)
        records.append({'ordinal': ordinal, 'filename': f'frame_{ordinal:06}.jpg',
                        'archive_entry': info.filename, 'position_m': pose[:3],
                        'quaternion_wxyz': pose[3:], 'zip_crc32': f'{info.CRC:08x}'})

    def download(pair):
        info, record = pair
        target = images / record['filename']
        if target.exists():
            data = target.read_bytes()
            if len(data) == info.file_size and binascii.crc32(data) & 0xffffffff == info.CRC:
                return record['ordinal']
        end = offsets[offsets.index(info.header_offset) + 1]
        data = read_entry(info, end, archive_size)
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.shape[:2] != (3072, 6144):
            raise ValueError('Unexpected panorama dimensions: ' + info.filename)
        partial = target.with_suffix('.jpg.partial')
        partial.write_bytes(data)
        partial.replace(target)
        return record['ordinal']

    print(f'Downloading {len(selected)} original 6144x3072 panoramas; ZIP CRC verified', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download, pair) for pair in zip(selected, records)]
        for completed, future in enumerate(as_completed(futures), 1):
            future.result()
            if completed % 20 == 0 or completed == len(futures):
                print(f'Images ready: {completed}/{len(futures)}', flush=True)
    video = root / (stem + '.mkv')
    partial = video.with_suffix('.mkv.partial')
    try:
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                        '-framerate', '2', '-start_number', '0', '-i',
                        str(images / 'frame_%06d.jpg'), '-frames:v', str(len(records)),
                        '-c:v', 'copy', '-f', 'matroska', str(partial)], check=True)
        cap = cv2.VideoCapture(str(partial))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            ok, frame = cap.read()
            if not ok or fps != 2 or count != len(records) or frame.shape[:2] != (3072, 6144):
                raise ValueError('Packaged sequence metadata does not match the original images')
        finally:
            cap.release()
        partial.replace(video)
    finally:
        partial.unlink(missing_ok=True)
    positions = np.array([row['position_m'] for row in records])
    original = np.array([pose_rows[item.filename.removeprefix('concourse/')][:3]
                         for item in image_entries])
    distances = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    with video.open('rb') as source:
        video_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    metadata = {
        'source': '360Loc Concourse / mapping daytime_360_1', 'reference': REFERENCE,
        'project_page': 'https://huajianup.github.io/research/360Loc/',
        'archive': ARCHIVE, 'archive_total_bytes': archive_size,
        'license': 'Separate media redistribution license not stated on the publisher page',
        'redistribution': 'Local sample only; images and MKV excluded from Git',
        'width': 6144, 'height': 3072, 'frames': len(records),
        'original_positions': len(image_entries), 'selected_stride': args.stride,
        'fps': 2, 'duration_seconds': len(records) / 2,
        'timing': 'Assigned 2 fps slideshow playback, not original capture timestamps',
        'encoding': 'Original JPEG packets copied into MJPEG MKV; no image re-encoding',
        'original_path_length_m': round(float(np.linalg.norm(np.diff(original, axis=0), axis=1).sum()), 3),
        'selected_path_length_m': round(float(distances.sum()), 3),
        'median_selected_gap_m': round(float(np.median(distances)), 3),
        'max_selected_gap_m': round(float(distances.max()), 3),
        'start_to_end_m': round(float(np.linalg.norm(original[-1] - original[0])), 3),
        'position_span_m': np.ptp(original, axis=0).round(3).tolist(),
        'pose_convention': 'Publisher: camera-to-world position and COLMAP camera axes',
        'size_bytes': video.stat().st_size,
        'sha256': video_hash,
        'images_directory': str(images.relative_to(ROOT)),
        'frames_metadata': str((directory / 'frames.json').relative_to(ROOT)),
    }
    (directory / 'frames.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    video.with_suffix('.source.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(f'Ready: {video}', flush=True)
    print(f"Published path: {metadata['original_path_length_m']} m; "
          f"start/end separation {metadata['start_to_end_m']} m", flush=True)
    preview = root / '360loc_concourse_walk_preview_2k.mp4'
    # The full-size MKV preserves JPEGs for training. H.264 is a separate,
    # smaller viewing copy for browsers which cannot play MJPEG Matroska.
    print('Creating browser preview (1920x960; use the 6K source for training)', flush=True)
    temporary_preview = preview.with_suffix('.mp4.partial')
    try:
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                        '-i', str(video), '-vf', 'scale=1920:960', '-an',
                        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22',
                        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                        '-f', 'mp4', str(temporary_preview)], check=True)
        temporary_preview.replace(preview)
    finally:
        temporary_preview.unlink(missing_ok=True)
    preview_metadata = {**metadata, 'width': 1920, 'height': 960,
                        'purpose': 'Browser viewing only; use the 6K MKV or original JPEGs for training',
                        'training_original': video.name,
                        'encoding': 'Downscaled H.264 preview, CRF 22',
                        'size_bytes': preview.stat().st_size}
    with preview.open('rb') as source:
        preview_metadata['sha256'] = hashlib.file_digest(source, 'sha256').hexdigest()
    preview.with_suffix('.source.json').write_text(
        json.dumps(preview_metadata, indent=2) + '\n', encoding='utf-8')
    print(f'Browser preview: {preview}', flush=True)


if __name__ == '__main__':
    main()
