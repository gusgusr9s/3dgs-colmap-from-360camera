"""Download the public moving-camera 360 sample linked by stella_vslam.

Keep the video local: the download page does not specify a separate media
redistribution license. The repository's Git ignore rules exclude this MP4.
"""

import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

import cv2


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "https://stella-cv.readthedocs.io/en/latest/simple_tutorial.html"
URL = ("https://drive.usercontent.google.com/download?"
       "id=1d8kADKWBptEqTF7jEVhKatBEdN7g0ikY&export=download&confirm=t")
ENTRY = "aist_living_lab_1/video.mp4"


def main():
    directory = ROOT / "data" / "raw"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "aist_living_lab_1.mp4"
    archive = directory / "aist_living_lab_1.zip"
    if not target.exists():
        if not archive.exists():
            partial = archive.with_suffix(".zip.partial")
            try:
                with urllib.request.urlopen(URL, timeout=60) as response, partial.open("wb") as output:
                    if "text/html" in response.headers.get("Content-Type", ""):
                        raise ValueError("Google Drive returned a page instead of the ZIP")
                    shutil.copyfileobj(response, output, 1024 * 1024)
                    expected = response.headers.get("Content-Length")
                if expected and partial.stat().st_size != int(expected):
                    raise ValueError("Incomplete archive download")
                # Confirm a ZIP before making it available to later invocations.
                with zipfile.ZipFile(partial) as contents:
                    contents.getinfo(ENTRY)
                partial.replace(archive)
            finally:
                partial.unlink(missing_ok=True)
        partial = target.with_suffix(".mp4.partial")
        try:
            with zipfile.ZipFile(archive) as contents:
                # Reading the complete entry verifies its ZIP CRC. Extract only
                # this known file; archive paths never become output paths.
                with contents.open(ENTRY) as source, partial.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    capture = cv2.VideoCapture(str(target))
    try:
        ok, frame = capture.read()
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not ok or fps <= 0 or count <= 0:
            raise ValueError("The downloaded video could not be decoded")
        height, width = frame.shape[:2]
    finally:
        capture.release()
    digest = hashlib.sha256()
    with target.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    metadata = {
        "source": "OpenVSLAM / AIST sample, linked by stella_vslam",
        "scene": "aist_living_lab_1", "reference": REFERENCE,
        "download_url": URL, "archive_entry": ENTRY,
        "license": "Separate media redistribution license not stated on the sample page",
        "redistribution": "Local test input only; video excluded from Git",
        "width": width, "height": height, "fps": fps, "frames": count,
        "duration_seconds": count / fps, "size_bytes": target.stat().st_size,
        "sha256": digest.hexdigest(), "projection": "equirectangular",
    }
    target.with_suffix(".source.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Ready: {target} ({width}x{height}, {count / fps:.2f}s, {fps:g} fps)")


if __name__ == "__main__":
    main()
