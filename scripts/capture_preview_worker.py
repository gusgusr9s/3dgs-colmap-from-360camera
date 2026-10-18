"""Bounded CPU-only source decoding for the interactive preview, isolated from training."""
import os
os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'threads;1'
os.environ['OMP_NUM_THREADS'] = '1'
from pathlib import Path
import json
import struct
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
cv2.setNumThreads(1)


def main():
    mode, filename = sys.argv[1:3]
    if mode == 'profile':
        from app.pipeline.view_suggestions import probe_source
        sys.stdout.write(json.dumps(probe_source(Path(filename))))
        return
    index = int(sys.argv[3])
    if Path(filename).suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
        frame = cv2.imread(filename)
    else:
        options = [cv2.CAP_PROP_N_THREADS, 1] if hasattr(cv2, 'CAP_PROP_N_THREADS') else []
        cap = cv2.VideoCapture(filename, cv2.CAP_FFMPEG, options)
        try:
            if not cap.isOpened():
                raise ValueError('เปิดวิดีโอไม่ได้')
            if index and not cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise ValueError('เลื่อนไปยังเฟรมที่เลือกไม่ได้')
            ok, frame = cap.read()
            if not ok:
                frame = None
        finally:
            cap.release()
    if frame is None:
        raise ValueError('อ่านเฟรมที่เลือกไม่ได้')
    if max(frame.shape[:2]) > 2048:
        ratio = 2048 / max(frame.shape[:2])
        frame = cv2.resize(frame, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
    height, width = frame.shape[:2]
    sys.stdout.buffer.write(struct.pack('<II', height, width))
    sys.stdout.buffer.write(frame.tobytes())


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
