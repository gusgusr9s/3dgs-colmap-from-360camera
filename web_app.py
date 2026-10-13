#!/usr/bin/env python3
"""Small upload/preview web UI for the 360 conversion prototype."""

from __future__ import annotations

import cgi
import gzip
import html
import json
import math
import mimetypes
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import sysconfig
import threading
import time
import uuid
from copy import deepcopy
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from pano_to_views import frames_from_input, perspective_map, project_frame
from capture_preview import sampling_plan, preview_jpeg
from spherical_to_brush import build_brush_dataset, create_dynamic_person_masks
from compute_runtime import colmap_version, select_colmap_compute, run_colmap_command
from reconstruction_preview import build_pose_preview, pose_input_signature
from task_history import TaskTimer, file_timestamp, format_duration, format_timestamp, run_id, utc_now
from task_estimates import PhaseTimer, estimate_status, compatible_history, training_samples, timestamp as estimate_timestamp
from resource_names import unique_label, reserve_job_directory, publish_upload
from benchmark_comparison import build_report as build_benchmark_report, render_report as render_benchmark_report
from view_suggestions import probe_source, suggest_view_settings
from view_templates import model_template, adapt_template
from training_selection import build_training_subset, validate_selection
from resource_previews import thumbnail_file
from quality_metrics import frame_metrics
from model_variants import (available_variant, variant_directory, start_fast_variant,
                            compression_record, compression_records, compression_directory,
                            rename_parent, delete_parent_variants, source_signature, write_record_json)
import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
UPLOADS = ROOT / "web_data" / "uploads"
JOBS = ROOT / "web_data" / "jobs"
MODELS = ROOT / "web_data" / "models"
RAW = ROOT / "data" / "raw"
EXPERIMENTS = ROOT / "web_data" / "experiments"
PREVIEWS = ROOT / "web_data" / "previews"
RAW_INSTA360 = {".insv", ".insp"}
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".mkv", ".webm"} | RAW_INSTA360
SOURCE_DIRECTORIES = {"raw": RAW, "uploads": UPLOADS}
DEFAULT_VIEW_SETTINGS = {
    "width": 768,
    "height": 768,
    "fov": 90,
    "views": 8,
    "sample_every": 8,
    "projection": "auto",
    "quality": "high",
    "start_seconds": 0,
    "end_seconds": 0,
}
QUALITY_PROFILES = {
    "preview": {"vertical_rings": 0, "steps": 10000, "max_resolution": 960, "max_splats": 1000000},
    "high": {"vertical_rings": 2, "steps": 20000, "max_resolution": 1536, "max_splats": 2000000},
    "ultra": {"vertical_rings": 2, "steps": 30000, "max_resolution": 2048, "max_splats": 4000000},
}
ACTIVE_TASKS = set()
ACTIVE_TASKS_LOCK = threading.Lock()
TASK_TOKENS = {}
JSON_LOCK = threading.RLock()
JOB_NAMING_LOCK = threading.RLock()
for directory in (UPLOADS, JOBS, MODELS, RAW):
    directory.mkdir(parents=True, exist_ok=True)


def safe_name(name: str) -> str:
    name = Path(name).name
    stem = "".join(c for c in Path(name).stem if c.isalnum() or c in "-_ ").strip() or "upload"
    suffix = Path(name).suffix.lower()
    return stem[:80] + suffix


@lru_cache(maxsize=256)
def _read_json_cached(filename, size, modified_ns):
    return json.loads(Path(filename).read_text(encoding="utf-8"))


def read_json(path: Path, default=None):
    try:
        with JSON_LOCK:
            stat = path.stat()
            if stat.st_size <= 1024 * 1024:
                return deepcopy(_read_json_cached(os.path.abspath(path), stat.st_size, stat.st_mtime_ns))
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {} if default is None else default


@lru_cache(maxsize=256)
def _view_summary_cached(filename, size, modified_ns):
    data = (_read_json_cached(filename, size, modified_ns) if size <= 1024 * 1024
            else json.loads(Path(filename).read_text(encoding='utf8')))
    summary = {key: data.get(key) for key in ('source_name', 'source_key', 'settings', 'source_mode', 'capture_plan')}
    summary['frame_count'] = len(data.get('frames', []))
    return summary


def view_summary(path):
    try:
        with JSON_LOCK:
            stat = path.stat()
            return deepcopy(_view_summary_cached(os.path.abspath(path), stat.st_size, stat.st_mtime_ns))
    except (OSError, ValueError, TypeError):
        return {}


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        with JSON_LOCK:
            for attempt in range(10):
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    # Windows can briefly hold a read handle (HTTP downloads or
                    # antivirus). Keep the previous complete JSON during retries.
                    if attempt == 9:
                        raise
                    time.sleep(.02)
    finally:
        temporary.unlink(missing_ok=True)


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def claim_task(job: str) -> bool:
    with ACTIVE_TASKS_LOCK:
        if job in ACTIVE_TASKS:
            return False
        directory = job_directory(job)
        if directory is None:
            return False
        lock_file = directory / ".task.lock"
        token = uuid.uuid4().hex
        for attempt in range(2):
            try:
                with lock_file.open("x", encoding="utf-8") as handle:
                    json.dump({"pid": os.getpid(), "token": token}, handle)
                break
            except FileExistsError:
                if task_lock_alive(lock_file):
                    return False
                if attempt == 1:
                    return False
                previous = lock_file.stat().st_mtime_ns
                if lock_file.stat().st_mtime_ns == previous and not task_lock_alive(lock_file):
                    lock_file.unlink(missing_ok=True)
        ACTIVE_TASKS.add(job)
        TASK_TOKENS[job] = token
        return True


def release_task(job: str):
    with ACTIVE_TASKS_LOCK:
        ACTIVE_TASKS.discard(job)
        token = TASK_TOKENS.pop(job, None)
        lock_file = JOBS / job / ".task.lock"
        if token and read_json(lock_file, {}).get("token") == token:
            lock_file.unlink(missing_ok=True)


def task_is_active(job: str) -> bool:
    with ACTIVE_TASKS_LOCK:
        return job in ACTIVE_TASKS or task_lock_alive(JOBS / job / ".task.lock")


def task_lock_alive(lock_file: Path) -> bool:
    if not lock_file.is_file():
        return False
    owner = read_json(lock_file, {})
    if not owner.get("pid"):
        return time.time() - lock_file.stat().st_mtime < 30
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(owner["pid"]))
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(owner["pid"]), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def clean_label(value: str, fallback: str) -> str:
    value = " ".join((value or "").strip().split())
    return value[:100] or fallback


def suggested_job_label(value, exclude=None):
    labels = [read_json(directory / 'job.json', {}).get('label', directory.name)
              for directory in JOBS.iterdir() if directory.is_dir() and directory.name != exclude]
    return unique_label(value, labels)


def create_job_record(source, source_key, settings, requested_label):
    with JOB_NAMING_LOCK:
        reserved = {read_json(path, {}).get('job') for path in MODELS.glob('*.meta.json')}
        reserved.update(path.stem.split('_')[0] for path in MODELS.glob('*.ply'))
        reserved.update(record.get('parent_info', {}).get('job') for record in compression_records())
        directory = reserve_job_directory(JOBS, reserved)
        label = suggested_job_label(clean_label(requested_label, source.stem), exclude=directory.name)
        write_json(directory / 'job.json', dict(label=label, source_name=source.name,
            source_key=source_key, settings=settings, created_at=utc_now()))
        return directory.name, label


def normalized_settings(values=None):
    values = values or {}
    settings = dict(DEFAULT_VIEW_SETTINGS)
    settings.update({key: values[key] for key in settings if key in values})
    settings["width"] = max(160, min(2048, int(settings["width"])))
    settings["height"] = max(160, min(2048, int(settings["height"])))
    settings["fov"] = max(30, min(150, float(settings["fov"])))
    settings["views"] = max(1, min(16, int(settings["views"])))
    settings["sample_every"] = max(1, min(10000, int(settings["sample_every"])))
    for key in ("start_seconds", "end_seconds"):
        settings[key] = float(settings[key] or 0)
        if not math.isfinite(settings[key]) or settings[key] < 0:
            raise ValueError("เวลาเริ่มและเวลาจบต้องเป็นตัวเลขตั้งแต่ 0 ขึ้นไป")
    if settings["projection"] not in {"auto", "panorama", "walkthrough"}:
        settings["projection"] = "auto"
    if settings["quality"] not in QUALITY_PROFILES:
        settings["quality"] = "high"
    return settings


def panorama_angles(views: int, quality: str):
    """Cover the horizon plus ceiling/floor without duplicating camera centers."""
    angles = [(float(yaw), 0.0) for yaw in np.linspace(0, 360, views, endpoint=False)]
    if QUALITY_PROFILES[quality]["vertical_rings"]:
        vertical_views = max(4, int(math.ceil(views / 2)))
        for pitch in (-55.0, 55.0):
            angles.extend(
                (float(yaw), pitch)
                for yaw in np.linspace(0, 360, vertical_views, endpoint=False)
            )
    return angles


def effective_sample_every(source: Path, requested: int) -> int:
    """Keep adjacent views close enough for SfM, including low-FPS panorama sequences."""
    if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return requested
    capture = cv2.VideoCapture(str(source))
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS)) if capture.isOpened() else 0.0
    finally:
        capture.release()
    if not np.isfinite(fps) or fps <= 0:
        return requested
    # Limit the temporal gap to roughly 0.3 seconds.  Normal 30 fps footage
    # keeps the requested default of 8; exported 5 fps panorama sequences
    # automatically use every frame instead of skipping ten camera positions.
    overlap_limit = max(1, int(fps * 0.3))
    return min(requested, overlap_limit)


def process_file(source: Path, job_dir: Path, width=768, height=768, fov=90, views=8,
                 sample_every=8, projection="auto", quality="high", progress_callback=None,
                 start_seconds=0, end_seconds=0, view_device=None, compute_callback=None, _gpu_worker=False):
    from view_compute import gpu_worker_views, view_mode
    from gpu_projection import create_projector
    mode = view_mode(view_device)
    computation = {"backend": "CPU", "device": "CPU", "requested": mode, "fallback_reasons": []}
    def report_compute(metadata):
        computation.update(metadata)
        if compute_callback:
            compute_callback(dict(computation))
    if not _gpu_worker:
        settings = dict(width=width, height=height, fov=fov, views=views, sample_every=sample_every,
                        projection=projection, quality=quality, start_seconds=start_seconds, end_seconds=end_seconds)
        count = gpu_worker_views(source, job_dir, settings, progress_callback, report_compute, mode)
        if count is not None:
            return count
    report_compute(computation)
    if source.suffix.lower() in RAW_INSTA360:
        raise ValueError(
            "ไฟล์ INSV/INSP ยังเป็นไฟล์ดิบแบบ dual-fisheye กรุณาเปิดด้วย Insta360 Studio "
            "แล้ว Export เป็นวิดีโอ 360 แบบ equirectangular อัตราส่วน 2:1 (MP4) ก่อนอัปโหลด"
        )
    requested_sample_every = sample_every
    plan = sampling_plan(probe_source(source), locals())
    sample_every = plan["effective_sample_every"]
    first = True
    frames = []
    source_mode = None
    projection_maps = []
    projector = None
    writers = None
    for frame_index, pano in frames_from_input(source, sample_every, plan["start_frame"], plan["end_frame"]):
        is_panorama = projection == "panorama" or (
            projection == "auto" and abs(pano.shape[1] / pano.shape[0] - 2.0) < 0.08
        )
        aspect_ratio = pano.shape[1] / pano.shape[0]
        if projection == "panorama" and abs(aspect_ratio - 2.0) >= 0.08:
            raise ValueError(
                f"ไฟล์นี้มีอัตราส่วน {aspect_ratio:.3f}:1 จึงไม่ใช่ภาพ 360 equirectangular 2:1 "
                "กรุณา Export จาก Insta360 Studio โดยเลือก 360 Video ไม่ใช่ Reframed Video"
            )
        source_mode = "panorama" if is_panorama else "walkthrough_video"
        if first:
            _, _, focal = perspective_map(width, height, fov, 0, 0)
            if is_panorama:
                projection_maps = [perspective_map(width, height, fov, yaw, pitch)[:2]
                                   for yaw, pitch in panorama_angles(views, quality)]
                if _gpu_worker:
                    projector, memory_reasons = create_projector(width, height, pano.shape, projection_maps)
                    report_compute({**projector.metadata, 'fallback_reasons': memory_reasons})
                    from concurrent.futures import ThreadPoolExecutor
                    writers = ThreadPoolExecutor(max_workers=4)
            first = False
        angles = panorama_angles(views, quality) if is_panorama else [(0.0, 0.0)]
        if projector:
            projector.upload(pano)
        gpu_views = projector.project_many() if projector else None
        pending_writes = []
        def write_view(name, view):
            target = job_dir / name
            write_path = job_dir / ("." + name) if projector else target
            if not cv2.imwrite(str(write_path), view, [cv2.IMWRITE_JPEG_QUALITY, 90]):
                raise RuntimeError(f"บันทึกภาพ {name} ไม่ได้ กรุณาตรวจพื้นที่ว่างและสิทธิ์โฟลเดอร์")
            if projector:
                write_path.replace(target)
        for view_index, (yaw, pitch) in enumerate(angles):
            view = (next(gpu_views) if projector else
                    project_frame(pano, width, height, fov, yaw, pitch, is_panorama,
                                  projection_maps[view_index] if is_panorama else None))
            name = f"frame_{frame_index:06d}_view_{view_index:02d}.jpg"
            if writers:
                pending_writes.append(writers.submit(write_view, name, view))
            else:
                write_view(name, view)
            frames.append({"file_path": name, "yaw_deg": yaw, "pitch_deg": pitch,
                           "frame_index": frame_index})
        for future in pending_writes:
            future.result()
        if progress_callback:
            progress_callback(len(frames))
    if not frames:
        raise ValueError("ไม่พบเฟรมในไฟล์ที่อัปโหลด")
    meta = {"camera_model": "PINHOLE", "fl_x": focal, "fl_y": focal,
            "cx": (width - 1) / 2, "cy": (height - 1) / 2,
            "w": width, "h": height, "frames": frames,
            "source_name": source.name, "source_mode": source_mode,
            "settings": {"width": width, "height": height, "fov": fov, "views": views,
                         "sample_every": sample_every, "projection": projection,
                         "quality": quality,
                         "start_seconds": start_seconds, "end_seconds": end_seconds,
                         "requested_sample_every": requested_sample_every},
            "capture_plan": plan, "view_compute": computation,
            "warning": "ใช้ placeholder poses; ต้องใช้ COLMAP/SLAM ก่อน train 3DGS"}
    (job_dir / "transforms.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if projector:
        projector.close()
    if writers:
        writers.shutdown()
    return len(frames)


def source_files():
    """Return files that the UI is allowed to process."""
    found = []
    for label, directory in (("data/raw", RAW), ("uploads", UPLOADS)):
        for path in sorted(directory.iterdir() if directory.exists() else []):
            if path.is_file() and path.suffix.lower() in ALLOWED:
                found.append((f"{label}:{path.name}", label, path))
    return found


def source_from_key(key):
    for candidate, _, path in source_files():
        if candidate == key:
            return path
    return None


def source_key_for_path(path: Path):
    path = path.resolve()
    if path.parent == RAW.resolve():
        return f"data/raw:{path.name}"
    if path.parent == UPLOADS.resolve():
        return f"uploads:{path.name}"
    return ""


def source_key_for_name(name: str):
    matches = [key for key, _, path in source_files() if path.name == name]
    return matches[0] if len(matches) == 1 else ""


def job_directory(job: str):
    if not job or job in {".", ".."} or "/" in job or "\\" in job or Path(job).name != job:
        return None
    directory = (JOBS / job).resolve()
    return directory if directory.parent == JOBS.resolve() and directory.is_dir() else None


def job_manifest(job: str):
    directory = job_directory(job)
    if directory is None:
        return {}
    stored = read_json(directory / "job.json", {})
    meta = view_summary(directory / "transforms.json")
    source_name = stored.get("source_name") or meta.get("source_name") or ""
    source_key = stored.get("source_key") or meta.get("source_key") or source_key_for_name(source_name)
    raw_settings = stored.get("settings") or meta.get("settings") or DEFAULT_VIEW_SETTINGS
    try:
        settings = normalized_settings(raw_settings)
    except (TypeError, ValueError):
        settings = dict(DEFAULT_VIEW_SETTINGS)
    return {
        "job": job,
        "label": clean_label(stored.get("label", ""), Path(source_name).stem or job),
        "source_name": source_name,
        "source_key": source_key,
        "settings": settings,
        "frames": meta.get('frame_count', 0),
        "source_mode": meta.get("source_mode") or "",
        "source_profile": stored.get("source_profile", {}),
        "capture_plan": meta.get("capture_plan") or stored.get("capture_plan", {}),
        "auto_model": stored.get("auto_model") is True,
    }


def save_job_manifest(job: str, manifest):
    directory = job_directory(job)
    if directory is None:
        raise ValueError("ไม่พบ job")
    stored = read_json(directory / "job.json", {})
    write_json(directory / "job.json", {**stored, **manifest})


def views_status(job: str):
    directory = job_directory(job)
    if directory is None:
        return {"state": "missing", "stage": "views", "progress": 0}
    status = read_json(directory / "views_status.json", {})
    if status:
        if (not status.get("started_at") and status.get("state") == "ready"
                and (directory / "transforms.json").is_file()):
            status["recorded_at"] = file_timestamp(directory / "transforms.json")
        return status
    if (directory / "transforms.json").is_file():
        return {"state": "ready", "stage": "views", "progress": 100,
                "step": "Perspective views ready",
                "recorded_at": file_timestamp(directory / "transforms.json")}
    return {"state": "waiting", "stage": "views", "progress": 0,
            "step": "Waiting to generate views"}


def model_file_for_job(job: str):
    status = model_status(job) or {}
    candidate = Path(status.get("model", "")).name
    if candidate and (MODELS / candidate).is_file():
        return MODELS / candidate
    models = model_files_for_job(job)
    return max(models, key=lambda path: path.stat().st_mtime) if models else None


def model_viewer_config(model_name: str) -> dict:
    """Keep each version's captured camera even after its input views change."""
    metadata = read_json(model_metadata_path(model_name), {})
    if "viewer_config" in metadata:
        return metadata["viewer_config"]
    for directory in JOBS.iterdir():
        if not directory.is_dir():
            continue
        status = model_status(directory.name) or {}
        if Path(status.get("model", "")).name != model_name and f"{directory.name}.ply" != model_name:
            continue
        return dataset_viewer_config(directory)
    return {}


def dataset_viewer_config(directory: Path, dataset=None) -> dict:
    transforms = read_json((dataset or directory / "brush_data") / "transforms.json", {})
    frames = transforms.get("frames", [])
    horizontal = [frame for frame in frames if abs(float(frame.get("pitch_deg", 0))) < 1e-6]
    candidates = horizontal or frames
    selected = candidates[len(candidates) // 2] if candidates else {}
    matrix = selected.get("transform_matrix")
    if not matrix or len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        return {}
    height, focal_y = float(transforms.get("h", 0)), float(transforms.get("fl_y", 0))
    fov_y = math.degrees(2 * math.atan(height / (2 * focal_y))) if height > 0 and focal_y > 0 else 55
    return {"transform_matrix": matrix, "fov_y": fov_y}


def model_camera_data(model):
    if Path(model).name != model or not (MODELS / model).is_file():
        raise ValueError('ไม่พบ Model')
    info = model_info(MODELS / model)
    job, identifier = info.get('job', ''), info.get('run_id', '')
    directory = job_directory(job)
    if directory is None or not re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{8}', identifier):
        return {'cameras': []}
    dataset = directory / 'model_runs' / identifier / 'dataset'
    data = read_json(dataset / 'transforms.json', {})
    groups = {}
    for ordinal, frame in enumerate(data.get('frames', [])):
        key = int(frame.get('frame_index', ordinal))
        score = (abs(float(frame.get('pitch_deg') or 0)), float(frame.get('yaw_deg') or 0) % 360)
        if key not in groups or score < groups[key][0]:
            groups[key] = (score, frame)
    cameras = []
    for index in sorted(groups):
        frame = groups[index][1]
        matrix = np.asarray(frame.get('transform_matrix'), dtype=np.float64)
        relative = Path(frame.get('file_path', ''))
        image = (dataset / relative).resolve()
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or not image.is_relative_to(dataset.resolve()) or not image.is_file():
            continue
        width, height = int(frame.get('w', data.get('w', 0))), int(frame.get('h', data.get('h', 0)))
        focal = float(frame.get('fl_x', data.get('fl_x', width / 2)))
        cameras.append({'frame_index': index, 'transform_matrix': matrix.tolist(), 'w': width, 'h': height,
                        'fov_x': math.degrees(2 * math.atan(width / (2 * focal))) if focal else 90,
                        'image_url': '/jobs/' + quote(image.relative_to(JOBS.resolve()).as_posix(), safe='/')})
    centers = np.asarray([item['transform_matrix'] for item in cameras])[:, :3, 3] if cameras else np.zeros((1, 3))
    return {'cameras': cameras, 'path_span': float(np.linalg.norm(np.ptp(centers, axis=0))),
            'world_units': data.get('world_units', 'relative'),
            'world_up': data.get('world_up'),
            'scene_radius': info.get('quality', {}).get('scene_radius_p90', 100),
            'view_run_id': info.get('view_run_id'), 'run_id': identifier}


def refinement_dialog(model, info=None):
    try:
        parent, dataset, _ = refinement_input(model)
        plan = refinement_plan(parent, dataset)
    except ValueError:
        return '', ''
    disabled = ' disabled' if task_is_active(parent['job']) else ''
    button = f'<button id="refine-open" type="button"{disabled}>ปรับละเอียดต่อ</button>'
    dialog = f'''<dialog id="refine-dialog"><h2>ปรับละเอียดจาก Model รุ่นนี้</h2><p>ใช้ checkpoint และภาพ/Camera Pose เดิม เทรนเพิ่ม {plan['steps']:,} steps ด้วยค่าที่ลดการเคลื่อนของจุด ได้ Model รุ่นใหม่ไว้เปรียบเทียบ</p><p>ช่วยปรับรายละเอียดที่มีข้อมูลรองรับ ส่วนที่ถูก mask หรือไม่เคยถ่ายยังต้องตรวจภาพจริง</p><form method="post" action="/actions"><input type="hidden" name="action" value="refine_model"><input type="hidden" name="model" value="{html.escape(model,quote=True)}"><footer><button id="refine-close" type="button">ปิด</button><button type="submit"{disabled}>เริ่มปรับละเอียด</button></footer></form></dialog>'''
    return button, dialog


def model_viewer_page(model, info, siblings='', quality_link='', renderer='spark', variant=None, compression=None):
    renderer = 'native' if renderer == 'native' else 'spark'
    base = 'https://cdn.jsdelivr.net/npm/three@0.186.1/'
    imports = ({'three': base + 'build/three.module.js', 'three/webgpu': base + 'build/three.webgpu.js',
                'three/tsl': base + 'build/three.tsl.js', 'three/addons/': base + 'examples/jsm/'} if renderer == 'native'
               else {'three': '/assets/vendor/three-0.180.0/three.module.js',
                     'three/addons/controls/OrbitControls.js': '/assets/vendor/three-0.180.0/OrbitControls.js',
                     'three/addons/postprocessing/Pass.js': '/assets/vendor/three-0.180.0/Pass.js',
                     '@sparkjsdev/spark': '/assets/vendor/spark-2.3.1/spark.module.js'})
    config = {'model': model, 'url': '/models/' + quote(model, safe=''),
              'cameras_url': '/api/model-cameras/' + quote(model, safe=''), 'renderer': renderer,
              'splats': info.get('quality', {}).get('published_splats', 0), 'initial_view': model_viewer_config(model)}
    fast = compression_record(compression) if compression else available_variant(MODELS / model)
    if fast and compression:
        fast = {**fast, 'record_id': fast['id']}
    full_url = config['url']
    selected = 'fast' if fast and renderer == 'spark' and variant != 'full' else 'full'
    config['variant'] = selected
    config['bytes'] = fast['bytes'] if selected == 'fast' else (MODELS / model).stat().st_size
    if selected == 'fast':
        config['url'] = '/compressed-download/' + fast['record_id'] + '.spz' if fast.get('record_id') else '/model-fast/' + quote(model, safe='') + '?v=' + fast['sha256'][:16]
    fast_disabled = ' disabled' if renderer != 'spark' else ''
    options = (f'<label for="model-variant">รุ่น</label><select id="model-variant">'
               f'<option value="fast"{fast_disabled}>โหลดเร็ว · {format_bytes(fast["bytes"])}</option>'
               f'<option value="full">เต็ม · {format_bytes((MODELS/model).stat().st_size)}</option></select>'
               '<span id="variant-note">โหลดเร็วยังคงจุดทั้งหมด · สีและค่าจุดถูกบีบอัด</span>') if fast else ''
    if fast and renderer == 'native':
        options += '<span id="variant-renderer-note">Native ใช้รุ่นเต็ม</span>'
    identifier = fast.get('record_id') if fast else None
    config['compression_id'] = identifier if selected == 'fast' else None
    compressed_download = (f'<a class="download-link" href="/compressed-download/{identifier}.spz" download="{html.escape(Path(model).stem,quote=True)}.spz">ดาวน์โหลด SPZ บีบอัด</a>'
                           f'<a href="/compression/{identifier}">Record รุ่นบีบอัด</a>') if identifier else '<button type="button" disabled>ดาวน์โหลด SPZ บีบอัด · ยังไม่มี</button>'
    compress_action = compression_form(model)
    button, dialog = refinement_dialog(model, info)
    repaired = [item for item in MODELS.glob('*.ply')
                if read_json(model_metadata_path(item.name), {}).get('reexported_from') == model]
    repaired_link = ''
    if repaired:
        newest = max(repaired, key=lambda item: item.stat().st_mtime)
        repaired_link = f'<a href="/model/{quote(newest.name,safe="")}">เปิดรุ่นแก้รายละเอียดแล้ว</a>'
    values = {'__TITLE__': html.escape(model), '__SIBLINGS__': siblings, '__QUALITY__': quality_link,
              '__MODEL_URL__': full_url, '__INPUTS__': '/model-inputs/' + quote(model, safe=''),
              '__MODEL_VARIANTS__': options,
              '__COMPRESSED_DOWNLOAD__': compressed_download, '__COMPRESSION_ACTION__': compress_action,
              '__LINEAGE__': lineage_markup(info, 'model').replace('</nav>', profile_resolution_markup(info.get('source_profile', {})) + '</nav>', 1), '__REFINE_BUTTON__': button, '__REFINE_DIALOG__': dialog,
              '__REPAIRED_MODEL__': repaired_link,
              '__MODEL_CONFIG_BUTTON__': '<button id="model-config-open" type="button">Check config</button>',
              '__MODEL_CONFIG_DIALOG__': model_config_dialog(model),
              '__IMPORTS__': json.dumps({'imports': imports}), '__CONFIG__': json.dumps(config, ensure_ascii=False).replace('<', '\\u003c')}
    page = (ROOT / 'model_viewer.html').read_text(encoding='utf-8')
    for key, value in values.items():
        page = page.replace(key, value)
    return page


def model_metadata_path(name: str) -> Path:
    return (MODELS / Path(name).name).with_suffix(".meta.json")


def model_config(name):
    if Path(name).name != name or not (MODELS / name).is_file(): raise ValueError('ไม่พบ Model')
    info = model_info(MODELS / name)
    dataset = {}
    job, identifier = info.get('job', ''), info.get('run_id', '')
    if job_directory(job) and re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{8}', identifier):
        dataset = read_json(JOBS / job / 'model_runs' / identifier / 'dataset' / 'transforms.json')
    settings = dataset.get('settings') or info.get('view_settings') or info.get('settings') or {}
    profile = info.get('source_profile', {})
    plan = dataset.get('capture_plan') or {}
    if not plan and settings and profile:
        try: plan = sampling_plan(profile, normalized_settings(settings))
        except (KeyError, ValueError, TypeError): pass
    return {'model':name, 'job':job, 'run_id':identifier, 'view_run_id':info.get('view_run_id'),
            'source_profile':profile, 'generated_settings':settings, 'capture_plan':plan,
            'quality':info.get('quality', {}), 'selected_frames':info.get('selected_frames'),
            'selection':info.get('selection'), 'actual_views':len(dataset.get('frames', [])) or None}


def model_config_dialog(name):
    data = model_config(name)
    profile, settings, plan, quality = (data[key] for key in ('source_profile','generated_settings','capture_plan','quality'))
    interval = plan.get('effective_sample_every', settings.get('sample_every'))
    rows = [('Source resolution', f"{profile.get('width','?')}×{profile.get('height','?')} px"),
            ('FPS / ความยาว Source', f"{profile.get('fps','?')} FPS / {profile.get('duration_seconds','?')} วินาที"),
            ('Profile / ภาพแต่ละมุม', f"{settings.get('quality','?')} / {settings.get('width','?')}×{settings.get('height','?')} px"),
            ('FOV / มุมต่อเฟรม', f"{settings.get('fov','?')}° / {plan.get('views_per_frame','?')} มุม"),
            ('เก็บเฟรมจริง', f'ทุก {interval} เฟรม · ข้าม {interval-1} เฟรม' if interval else 'ไม่มีข้อมูล'),
            ('sample_every ที่ขอ', plan.get('requested_sample_every', settings.get('requested_sample_every','?'))),
            ('ระยะเวลาระหว่างเฟรม', f"{plan.get('sample_gap_seconds','?')} วินาทีในไฟล์ Source"),
            ('ช่วงที่เลือก', f"{plan.get('start_seconds','?')}–{plan.get('end_seconds','?')} วินาที"),
            ('จำนวนภาพ', data['actual_views'] or quality.get('perspective_views', quality.get('registered_views','?'))),
            ('เทรนรอบนี้ / รวม', f"{quality.get('training_steps','?')} / {quality.get('cumulative_training_steps',quality.get('training_steps','?'))} steps"),
            ('จำนวน Gaussians จริง', quality.get('published_splats','?')),
            ('เพดาน splats', quality.get('max_splats','?')),
            ('Camera Pose', quality.get('pose_source', 'COLMAP / ไม่มีข้อมูล')),
            ('Masks', quality.get('mask_model','ไม่มีข้อมูล')),
            ('PSNR / ภาพทดสอบ', f"{quality.get('eval_psnr_mean_db','?')} dB / {quality.get('eval_views','?')} ภาพ")]
    body = '<table><tbody>' + ''.join(f'<tr><th style="text-align:left;padding:8px">{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>' for key,value in rows) + '</tbody></table>'
    if data['selection']:
        body += '<p>ชุดภาพที่ใช้: ' + html.escape(str(data['selection'].get('mode', 'selected_positions'))) + '</p>'
    if quality.get('comparison_limitation'):
        body += '<p>' + html.escape(quality['comparison_limitation']) + '</p>'
    return f'<dialog id="model-config-dialog"><h2>Check config · Model รุ่นนี้</h2><p>ค่าที่บันทึกกับรุ่นนี้: {html.escape(name)}</p>{body}<p><a href="/api/model-config/{quote(name,safe="")}" target="_blank">เปิด config JSON</a></p><footer><button id="model-config-close" type="button" autofocus>ปิด</button></footer></dialog>'


def model_files_for_job(job: str):
    names = {f"{job}.ply", Path((model_status(job) or {}).get("model", "")).name}
    for metadata in MODELS.glob("*.meta.json"):
        info = read_json(metadata, {})
        if info.get("job") == job:
            names.add(Path(info.get("model", "")).name)
    directory = job_directory(job)
    if directory:
        for status_file in directory.glob("model_runs/*/status.json"):
            names.add(Path(read_json(status_file, {}).get("model", "")).name)
    return [MODELS / name for name in names if name.endswith(".ply") and (MODELS / name).is_file()]


def model_info(model: Path):
    info = read_json(model_metadata_path(model.name), {})
    if not info:
        for directory in JOBS.iterdir():
            if not directory.is_dir():
                continue
            status = model_status(directory.name) or {}
            if status.get("model") == model.name or model.name == f"{directory.name}.ply":
                info = {**(status if status.get("model") == model.name else {}),
                        "job": directory.name, "viewer_config": dataset_viewer_config(directory)}
                break
    return {"recorded_at": file_timestamp(model), **info, "model": model.name}


def preview_images(directory):
    """Pick representative input images without following paths outside a dataset."""
    if directory is None:
        return []
    frames = [frame for frame in read_json(directory / 'transforms.json', {}).get('frames', [])
              if isinstance(frame, dict) and isinstance(frame.get('file_path'), str)]
    horizontal = [frame for frame in frames if frame.get('pitch_deg') in (None, 0, '0', '0.0')]
    candidates = horizontal or frames
    paths = []
    for index in sorted(set(np.linspace(0, len(candidates) - 1, min(len(candidates), 4), dtype=int))):
        image = (directory / candidates[index].get('file_path', '')).resolve()
        if image.is_relative_to(directory.resolve()) and image.is_file() and image.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
            paths.append(image)
    if not paths:
        paths = [path.resolve() for path in sorted(directory.glob('frame_*.jpg'))[:4]
                 if path.resolve().is_relative_to(directory.resolve())]
    return paths


def resource_preview_assets(kind, key, metadata=None):
    if kind == 'source':
        prefix, _, filename = key.partition(':')
        source = source_from_route({'data/raw': 'raw', 'uploads': 'uploads'}.get(prefix), filename)
        if source is None:
            raise ValueError('ไม่พบ Source')
        if source.suffix.lower() in RAW_INSTA360:
            return [], 'image', 'ต้อง Export 360 ก่อน'
        mode = 'video' if source.suffix.lower() in {'.mp4', '.mov', '.mkv', '.webm'} else 'image'
        return [source], mode, 'Source'
    if kind == 'views':
        directory = job_directory(key)
        if directory is None:
            raise ValueError('ไม่พบ Views')
        return preview_images(directory), 'mosaic', 'Views · ภาพรวม'
    if kind == 'view-image':
        image = (JOBS / key).resolve()
        if not image.is_relative_to(JOBS.resolve()) or not image.is_file() or image.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
            raise ValueError('ไม่พบภาพ View')
        return [image], 'image', 'View'
    if kind == 'model':
        model = (MODELS / key).resolve()
        if not key or '/' in key or '\\' in key or model.parent != MODELS.resolve() or not model.is_file() or model.suffix.lower() != '.ply':
            raise ValueError('ไม่พบ Model')
        info = model_info(model) if metadata is None else metadata
        directory = job_directory(info.get('job', ''))
        identifier = info.get('run_id', '')
        if directory and re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{8}', identifier or ''):
            step = int(info.get('quality', {}).get('training_steps') or 0)
            render_dir = directory / 'model_runs' / identifier / 'brush_export' / f'eval_{step}'
            renders = [path.resolve() for path in sorted(render_dir.glob('*.png'))
                       if path.resolve().is_relative_to(directory) and path.is_file()]
            if renders:
                scores = {}
                for frame in read_json(directory / 'model_runs' / identifier / 'dataset' / 'transforms.json', {}).get('frames', []):
                    try:
                        yaw = float(frame.get('yaw_deg') or 0) % 360
                        scores[Path(frame['file_path']).stem] = (abs(float(frame.get('pitch_deg') or 0)), min(yaw, 360 - yaw))
                    except (KeyError, TypeError, ValueError):
                        continue
                best = min(scores.get(path.stem, (0, 0)) for path in renders)
                preferred = [path for path in renders if scores.get(path.stem, (0, 0)) == best]
                return [preferred[len(preferred) // 2]], 'image', 'Model · Render'
        return [model], 'ply', 'Model · ภาพรวมจุด 3D'
    raise ValueError('ไม่พบชนิด Preview')


def resource_preview_url(kind, key):
    return '/api/resource-preview?' + urlencode({'kind': kind, 'key': key})


def resource_preview_markup(kind, key, href, metadata=None, run=False, deferred=False):
    # Resolve and validate image files only when their thumbnail is requested.
    label = {'source': 'Source', 'views': 'Views · ภาพรวม', 'model': 'Model · Preview', 'view-image': 'View'}.get(kind, 'Preview')
    if kind == 'source' and Path(key.partition(':')[2]).suffix.lower() in RAW_INSTA360:
        label = 'ต้อง Export 360 ก่อน'
    url = resource_preview_url(kind, key)
    extra = ' data-run-preview="1"' if run else ''
    version = (views_status(key).get('run_id') or '') if kind == 'views' else ''
    return (f'<a class="resource-preview" href="{html.escape(href, quote=True)}" data-preview-kind="{kind}"{extra}>'
            f'<img {"data-preview-src" if deferred else "src"}="{html.escape(url, quote=True)}" alt="{html.escape(label + ": " + key, quote=True)}" width="384" height="216"'
            f' data-view-run="{html.escape(version, quote=True)}" loading="lazy" decoding="async" onerror="this.hidden=true">'
            f'<span>{html.escape(label)}</span></a>')


def archive_existing_models(job: str):
    manifest = job_manifest(job)
    for model in model_files_for_job(job):
        metadata = model_metadata_path(model.name)
        previous = read_json(metadata,{})
        info = dict(previous) if previous else model_info(model)
        for key in ('job','label','source_key','source_name','source_profile'):
            if not info.get(key) and manifest.get(key):
                info[key] = manifest[key]
        if info != previous:
            write_json(metadata, info)


def model_runs(job: str):
    directory = job_directory(job)
    runs = [read_json(path, {}) for path in directory.glob("model_runs/*/status.json")] if directory else []
    represented = {run.get("model") for run in runs}
    for model in model_files_for_job(job):
        if model.name not in represented:
            runs.append({**model_info(model), "state": "done", "stage": "model",
                         "progress": 100, "step": "Model ready"})
    return sorted(runs, key=lambda run: run.get("started_at") or run.get("recorded_at", ""), reverse=True)


def lineage_source(info):
    """Resolve the current source name through its job; retain archived lineage."""
    current = job_manifest(info.get("job", ""))
    key = info.get("source_key") or current.get("source_key", "")
    name = info.get("source_name") or current.get("source_name") or key.partition(":")[2]
    source = source_from_key(key)
    return key, name, source


def source_link(info):
    key, name, source = lineage_source(info)
    if source is None:
        note = (' · ไฟล์ต้นฉบับไม่อยู่แล้ว' if key else ' · ยังระบุ Source ต้นทางไม่ได้') if name else ''
        return html.escape(name or "ไม่มีข้อมูล Source") + note
    kind = "raw" if key.startswith("data/raw:") else "uploads"
    return f'<a href="{source_route(kind, source, preview=True)}">{html.escape(source.name)}</a>'


def models_url(job="", view_run=None, source=""):
    query = {}
    if job:
        query["job"] = job
    if view_run is not None:
        query["view_run"] = view_run or "legacy"
    if source:
        query["source"] = source
    return "/models" + ("?" + html.escape(urlencode(query), quote=True) if query else "")


def quality_available(job, identifier):
    directory = job_directory(job)
    if directory is None or not re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{8}', identifier or ''):
        return False
    run_dir = directory / 'model_runs' / identifier
    status = read_json(run_dir / 'status.json', {})
    if not (run_dir / 'dataset').is_dir() and status.get('view_run_id') != views_status(job).get('run_id'):
        return False
    dataset = run_dir / 'dataset' if (run_dir / 'dataset').is_dir() else directory / 'brush_data'
    if not (dataset / 'transforms.json').is_file():
        return False
    steps = status.get('quality', {}).get('training_steps', 0)
    return any((dataset / 'images' / (image.stem+'.jpg')).is_file()
               for image in (run_dir / 'brush_export' / f'eval_{steps}').glob('*.png'))


def lineage_markup(info, current="views"):
    """Use identical source / views / models navigation on every detail screen."""
    job = info.get("job", "")
    manifest = job_manifest(job)
    view_run = info.get("view_run_id") if current == "model" else views_status(job).get("run_id")
    steps = [f'<span>Source · {source_link(info)}</span>']
    if manifest:
        label = 'Views / งาน (ชุดปัจจุบัน)' if current == "model" and view_run != views_status(job).get("run_id") else 'Views / งาน'
        steps.append(f'<a href="/job/{quote(job, safe="")}">{label} · {html.escape(manifest["label"])}</a>')
        if current != "model" or view_run == views_status(job).get("run_id"):
            steps.append(f'<a href="/poses/{quote(job, safe="")}">Camera Preview</a>')
        steps.append(f'<a href="{models_url(job, view_run)}">Models จาก Views ชุดนี้</a>')
        steps.append(f'<a href="{models_url(job)}">Models ทุกชุดของงานนี้</a>')
    elif job:
        steps.append(f'<span>Views · งาน {html.escape(job)} ถูกลบแล้ว</span>')
    else:
        steps.append('<span>Model ที่นำเข้าโดยตรง</span>')
    if current == "model":
        if info.get('model'):
            steps.append(f'<a href="/model-inputs/{quote(info["model"],safe="")}">ภาพที่ใช้เทรนรุ่นนี้</a>')
        steps.append(f'<strong>{html.escape(info.get("model", "Model"))}</strong>')
    version = html.escape(view_run or "งานเดิม · ไม่มีรหัสชุด Views")
    return f'<nav class="lineage" aria-label="เส้นทาง Source Views Models">{"<span class=separator>›</span>".join(steps)}<small>ชุด Views: {version}</small></nav>'


def model_matches(info, job="", view_run=None, source=""):
    if job and info.get("job") != job:
        return False
    if view_run is not None and (info.get("view_run_id") or "legacy") != (view_run or "legacy"):
        return False
    return not source or lineage_source(info)[0] == source


def source_generation_modal(source, key, identifier="source-create"):
    if source.suffix.lower() in RAW_INSTA360:
        return "", ""
    key_e = html.escape(key, quote=True)
    body = f'''<form id="{identifier}-form" class="modal-body" method="post" action="/actions" data-working data-settings-source="{key_e}" data-settings-mode="new"><input type="hidden" name="action" value="generate"><input type="hidden" name="source" value="{key_e}"><div class="field full"><label>ชื่อชุด Views / งาน</label><input name="label" value="{html.escape(suggested_job_label(source.stem), quote=True)}" maxlength="100"><small class="muted">หากชื่อซ้ำ ระบบจะเติมลำดับให้โดยเก็บงานเดิมไว้</small></div>{settings_fields(DEFAULT_VIEW_SETTINGS, suggestions=True, auto_model=True)}</form>'''
    button = f'<button class="primary" type="button" data-modal="{identifier}">+ สร้าง 3D / Preview Views</button>'
    dialog = modal(identifier, f"สร้างจาก {source.name}", body, f'<button class="primary" type="submit" form="{identifier}-form">เริ่มประมวลผล</button>')
    return button, dialog


def allocate_model_run(job: str, selected_frames=None, selection_signature=None):
    archive_existing_models(job)
    identifier = run_id()
    manifest = job_manifest(job)
    directory = job_directory(job)
    stored = read_json(directory / "job.json", {})
    view_settings = read_json(directory / "transforms.json", {}).get("settings", {})
    source_profile = stored.get("source_profile", {})
    source = source_from_key(manifest.get("source_key", ""))
    if not source_profile and source is not None:
        try:
            source_profile = probe_source(source)
        except (ValueError, OSError):
            pass
    status = {"run_id": identifier, "job": job, "label": manifest.get("label", job),
              "settings": manifest.get("settings", {}), "created_at": utc_now(),
              "view_settings": view_settings, "source_profile": source_profile,
              "source_name": manifest.get("source_name"),
              "source_key": manifest.get("source_key"), "view_run_id": views_status(job).get("run_id"),
              "selected_frames": selected_frames, "selection_signature": selection_signature,
              "state": "running", "stage": "model", "progress": 0, "step": "Starting model generation"}
    write_json(JOBS / job / "model_runs" / identifier / "status.json", status)
    return identifier


def model_status_files():
    for directory in JOBS.iterdir():
        if directory.is_dir():
            yield directory / "3d_status.json"
            yield from directory.glob("model_runs/*/status.json")


def delete_model_file(name: str):
    delete_parent_variants(Path(name).name)
    (MODELS / Path(name).name).unlink(missing_ok=True)
    model_metadata_path(name).unlink(missing_ok=True)
    for status_file in model_status_files():
        status = read_json(status_file, {})
        if status.get("model") == name:
            status.update(state="deleted", progress=0, step="Model deleted", deleted_at=utc_now())
            write_json(status_file, status)


def rename_model_file(name: str, new_name: str):
    metadata = model_info(MODELS / name)
    (MODELS / name).rename(MODELS / new_name)
    rename_parent(name, MODELS / new_name)
    metadata["model"] = new_name
    write_json(model_metadata_path(new_name), metadata)
    if model_metadata_path(new_name) != model_metadata_path(name):
        model_metadata_path(name).unlink(missing_ok=True)
    for status_file in model_status_files():
        status = read_json(status_file, {})
        if status.get("model") == name:
            status["model"] = new_name
            write_json(status_file, status)


def jobs_for_source(source_key: str, source_name: str):
    matches = []
    for directory in JOBS.iterdir():
        if not directory.is_dir():
            continue
        manifest = job_manifest(directory.name)
        # job_manifest resolves a legacy filename only when it is unique.
        # Ambiguous raw/upload names must not link or cascade-delete each other.
        if source_key and manifest.get("source_key") == source_key:
            matches.append(directory.name)
    return matches


def source_view_history(source_key: str, profile):
    """Use recorded settings for the actual dataset, excluding changed sources."""
    records, seen = [], set()
    def add(job, identity, level, settings, quality, source_profile, recorded_at, fallback_file=None):
        fingerprint = (source_profile or {}).get("fingerprint")
        if fingerprint and (fingerprint != profile["fingerprint"]
                            or source_profile.get("modified_ns", profile["modified_ns"]) != profile["modified_ns"]):
            return
        if not fingerprint and (fallback_file is None or not fallback_file.is_file()
                                or profile["modified_ns"] > fallback_file.stat().st_mtime_ns + 1_000_000_000):
            return
        if not settings or (job, identity, level) in seen:
            return
        try:
            settings = normalized_settings(settings)
        except (TypeError, ValueError, OverflowError):
            return
        registered = quality.get("registered_panoramas", quality.get("registered_views", 0))
        total = quality.get("input_panoramas", quality.get("input_views", registered))
        coverage = float(registered) / max(1, float(total))
        psnr = float(quality.get("eval_psnr_mean_db", 0))
        if level > 0 and ((total and coverage < .5) or (psnr and psnr < 18)):
            return
        seen.add((job, identity, level))
        records.append({"job": job, "run_id": identity, "level": level, "settings": settings,
                        "coverage": coverage, "psnr": psnr, "recorded_at": recorded_at or ""})

    for job in jobs_for_source(source_key, profile["name"]):
        directory = job_directory(job)
        stored = read_json(directory / "job.json", {})
        meta_file = directory / "transforms.json"
        meta = read_json(meta_file, {})
        actual = dict(meta.get("settings") or stored.get("settings") or {})
        if meta.get("w") and meta.get("h"):
            actual.update(width=meta["w"], height=meta["h"])
        if "quality" not in actual and meta.get("frames"):
            actual["quality"] = "high" if any(frame.get("pitch_deg", 0) for frame in meta["frames"]) else "preview"
        source_profile = stored.get("source_profile", {})
        view_records = {item.get("run_id"): item for item in (
            read_json(path, {}) for path in directory.glob("view_runs/*/status.json"))}
        view = views_status(job)
        if view.get("state") == "ready" and meta.get("frames"):
            add(job, view.get("run_id", "legacy-views"), 0, actual, {}, source_profile,
                view.get("finished_at") or view.get("recorded_at"), meta_file)
        for item in view_records.values():
            if item.get("state") == "ready":
                add(job, item.get("run_id"), 0, item.get("settings"), {}, item.get("source_profile"),
                    item.get("finished_at"), meta_file)
        pose = read_json(directory / "pose_status.json", {})
        if pose.get("state") == "ready" and pose.get("quality", {}).get("sparse_points", 0) >= 1000:
            add(job, "poses", 1, actual, pose["quality"], source_profile, pose.get("finished_at"), meta_file)
        for item in model_runs(job):
            if item.get("state") not in {"done", "deleted"} or not item.get("quality"):
                continue
            if item.get("selected_frames"):
                continue
            snapshot = read_json(model_metadata_path(item.get("model", "")), {})
            item = {**snapshot, **item}
            captured = view_records.get(item.get("view_run_id"), {})
            settings = item.get("view_settings") or captured.get("settings")
            if not settings:
                # Models created before settings snapshots may only reuse the
                # current dataset if their recorded input signature still matches.
                if item.get("input_signature") and item["input_signature"] != pose_input_signature(directory):
                    continue
                settings = actual
            add(job, item.get("run_id", item.get("model", "legacy-model")), 2, settings,
                item.get("quality", {}), item.get("source_profile") or source_profile,
                item.get("finished_at") or item.get("recorded_at"), meta_file)
    # Retained model metadata can supply evidence even after a job is deleted.
    for metadata in MODELS.glob("*.meta.json"):
        item = read_json(metadata, {})
        if (not item.get("job") or job_directory(item["job"]) is not None
                or item.get("source_profile", {}).get("fingerprint") != profile["fingerprint"]
                or not item.get("quality")):
            continue
        add(item["job"], item.get("run_id", item.get("model")), 2,
            item.get("view_settings") or item.get("settings"), item["quality"], item["source_profile"],
            item.get("finished_at"))
    return records


def source_view_suggestions(source_key: str, quality=None, projection=None, fov=None):
    source = source_from_key(source_key)
    if source is None:
        raise ValueError("ไม่พบ source ต้นฉบับ")
    profile = probe_source(source)
    result = suggest_view_settings(profile, source_view_history(source_key, profile), quality, projection, fov)
    result['templates'] = suggested_templates(profile)
    return result


def suggested_templates(profile):
    recipes = []
    records = {record.get('model'):record for record in
               read_json(ROOT / 'view_template_presets.json').get('templates', [])}
    for model in MODELS.glob('*.ply'):
        records[model.name] = model_info(model)
    for record in records.values():
        template = model_template(record)
        if template:
            adapted = adapt_template(template, profile)
            if adapted:
                adapted['model_available'] = (MODELS / template['model']).is_file()
                recipes.append(adapted)
    unique, seen = [], set()
    for recipe in sorted(recipes, key=lambda item: item['recorded_at'], reverse=True):
        settings = recipe['settings']
        signature = (recipe['source_profile'].get('fingerprint'),
                     *(settings.get(key) for key in DEFAULT_VIEW_SETTINGS))
        if signature in seen: continue
        seen.add(signature); unique.append(recipe)
    return unique


def profile_resolution_markup(profile, note=''):
    if not profile.get('width') or not profile.get('height'): return ''
    parts = [f"Source resolution: {profile['width']}×{profile['height']} px"]
    if profile.get('kind') == 'video':
        fps, duration = profile.get('fps'), profile.get('duration_seconds')
        parts += [f'{fps:.2f} FPS' if fps else 'ไม่ทราบ FPS']
        if duration: parts.append(f'{duration:.1f} วินาที')
        if profile.get('frame_count'): parts.append(f"{profile['frame_count']:,} เฟรม")
    if note: parts.append(str(note))
    return '<div class="resource-meta source-resolution">' + html.escape(' · '.join(parts)) + '</div>'


def source_resolution_markup(source):
    try:
        profile = probe_source(source)
        provenance = read_json(source.with_suffix('.source.json'))
        note = provenance.get('fps_note') or provenance.get('timing')
        return profile_resolution_markup(profile, note)
    except (OSError, ValueError, cv2.error) as exc:
        return '<div class="resource-meta source-resolution">Source resolution: ' + html.escape(str(exc)) + '</div>'


def job_config(job):
    directory = job_directory(job)
    if directory is None: raise ValueError('ไม่พบงาน')
    meta = read_json(directory / 'transforms.json')
    status = views_status(job)
    manifest = job_manifest(job)
    # Never show later editable settings as the recipe of existing images.
    settings = meta.get('settings') or status.get('settings') or {}
    profile = status.get('source_profile') or manifest.get('source_profile') or {}
    plan = meta.get('capture_plan') or status.get('capture_plan') or {}
    ids = sorted({f['frame_index'] for f in meta.get('frames', []) if f.get('frame_index') is not None})
    requested = plan.get('requested_sample_every', settings.get('requested_sample_every', settings.get('sample_every')))
    interval = plan.get('effective_sample_every', settings.get('sample_every'))
    training = QUALITY_PROFILES.get(manifest.get('settings', {}).get('quality', 'high'), QUALITY_PROFILES['high'])
    models = [model_info(model) for model in model_files_for_job(job)
              if model_info(model).get('view_run_id') == status.get('run_id')]
    return {'job': job, 'view_run_id': status.get('run_id'), 'source_profile': profile,
            'generated_settings': settings, 'configured_settings': manifest.get('settings'),
            'capture_plan': plan, 'requested_sample_every': requested, 'effective_sample_every': interval,
            'skipped_between_samples': interval - 1 if interval else None,
            'actual_positions': len(ids), 'actual_views': len(meta.get('frames', [])),
            'first_frame': ids[0] if ids else None, 'last_frame': ids[-1] if ids else None,
            'pose_quality': {**read_json(directory / 'pose_status.json').get('quality', {}), **(models[-1].get('quality', {}) if models else {})},
            'view_compute': meta.get('view_compute') or status.get('view_compute') or {},
            'next_training_profile': training, 'models': models}


def config_dialog(job, identifier):
    data = job_config(job)
    settings, plan, profile = data['generated_settings'], data['capture_plan'], data['source_profile']
    interval, skipped = data['effective_sample_every'], data['skipped_between_samples']
    rows = [('Source resolution', f"{profile.get('width', '?')}×{profile.get('height', '?')} px"),
            ('FPS / ความยาว', f"{profile.get('fps', '?')} FPS / {profile.get('duration_seconds', '?')} วินาที"),
            ('Profile ที่สร้าง Views', settings.get('quality', 'ไม่ทราบ')),
            ('ภาพแต่ละมุม', f"{settings.get('width', '?')}×{settings.get('height', '?')} px"),
            ('FOV / Projection', f"{settings.get('fov', '?')}° / {settings.get('projection', '?')}"),
            ('การเก็บเฟรมจริง', f'เก็บ 1 ทุก {interval} เฟรม · ข้าม {skipped} เฟรมระหว่างภาพ' if interval else 'ไม่มีข้อมูล'),
            ('sample_every ที่ขอ', data['requested_sample_every']),
            ('ระยะเวลาระหว่างเฟรม', f"{plan.get('sample_gap_seconds', '?')} วินาที"),
            ('ช่วงวิดีโอ', f"{plan.get('start_seconds', '?')}–{plan.get('end_seconds', '?')} วินาที"),
            ('เฟรมต้นฉบับที่ใช้', f"{data['first_frame']}–{data['last_frame']} (นับจาก 0)"),
            ('จำนวนจริง', f"{data['actual_positions']:,} ตำแหน่ง / {data['actual_views']:,} Views"),
            ('มุมต่อเฟรม', f"{plan.get('views_per_frame', '?')} · แนวนอน {settings.get('views', '?')} + เงย/ก้ม"),
            ('Camera poses', data['pose_quality'].get('pose_source', 'COLMAP / ยังไม่มี pose')),
            ('Masks', data['pose_quality'].get('mask_model', 'ยังไม่มีข้อมูล'))]
    table = '<table><tbody>' + ''.join(f'<tr><th style="text-align:left;padding:8px">{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>' for key,value in rows) + '</tbody></table>'
    compute = data['view_compute']
    if compute:
        table += '<p>Views compute: ' + html.escape(str(compute.get('device', 'CPU')) + ' · ' + str(compute.get('backend', 'CPU'))) + '</p>'
        if compute.get('fallback_reasons'):
            table += '<details><summary>เหตุผลที่เปลี่ยน backend</summary><p>' + html.escape(' · '.join(compute['fallback_reasons'])) + '</p></details>'
    directions = ', '.join(f"yaw {item['yaw']:g}° / pitch {item['pitch']:g}°" for item in plan.get('directions', []))
    training = data['next_training_profile']
    next_profile = (data['configured_settings'] or {}).get('quality', 'high')
    body = '<div class="modal-body"><p>ค่าที่บันทึกตอนสร้าง Views ชุดปัจจุบัน</p>' + table
    if data['generated_settings'] and normalized_settings(data['generated_settings']) != data['configured_settings']:
        body += '<p class="suggestion-warning">ค่าที่ตั้งไว้มีการเปลี่ยนหลังสร้างภาพ ดูค่าจริงของภาพในตารางด้านบน</p>'
    if directions: body += '<details><summary>ดูมุมกล้องทั้งหมด</summary><p>' + html.escape(directions) + '</p></details>'
    body += f'<p>สร้าง Model รอบใหม่: {html.escape(next_profile)} · {training["steps"]:,} steps · สูงสุด {training["max_splats"]:,} splats · ขนาดภาพ train สูงสุด {training["max_resolution"]} px</p>'
    for model in data['models']:
        quality = model.get('quality', {})
        body += f'<p>Model ที่สำเร็จ: <a href="/model/{quote(model["model"], safe="")}">{html.escape(model["model"])}</a> · ใช้จริง {quality.get("training_steps", "?")} steps · PSNR {quality.get("eval_psnr_mean_db", "?")} dB</p>'
    body += f'<p><a href="/api/job-config/{quote(job, safe="")}" target="_blank">เปิด config JSON</a></p></div>'
    return modal(identifier, 'Check config · Views', body, '')


def cleanup_job_derivatives(job: str):
    directory = job_directory(job)
    if directory is None:
        raise ValueError("ไม่พบ job")
    archive_existing_models(job)
    for image in directory.glob("frame_*.jpg"):
        image.unlink(missing_ok=True)
    for name in ("_input_images", "ns_data", "spherical_colmap", "brush_data", "brush_export"):
        target = directory / name
        if target.is_dir():
            shutil.rmtree(target)
    for name in ("transforms.json", "3d.log", "views_status.json",
                 "pose_status.json", "pose_preview.json", "poses.log"):
        (directory / name).unlink(missing_ok=True)


def generate_views(job: str, source_key: str, settings, reprocess=False, auto_model=False):
    directory = job_directory(job)
    timer = TaskTimer()
    identifier = run_id()
    context = {"source_key": source_key}
    succeeded = False
    def save(state, **extra):
        status = {**context, "state": state, "stage": "views", "run_id": identifier,
                  **timer.snapshot(finished=state in {"ready", "error"}), **extra}
        status = estimate_status(status)
        write_json(directory / "views_status.json", status)
        write_json(directory / "view_runs" / identifier / "status.json", status)
    try:
        if directory is None:
            raise RuntimeError("ไม่พบ job")
        source = source_from_key(source_key)
        if source is None:
            raise RuntimeError("ไม่พบ source ต้นฉบับ กรุณาตรวจว่าไฟล์ยังอยู่")
        settings = normalized_settings(settings)
        source_profile = probe_source(source)
        plan = sampling_plan(source_profile, settings)
        if auto_model and (plan["positions"] or 0) < 6:
            raise ValueError("สร้างโมเดลอัตโนมัติต้องมีอย่างน้อย 6 เฟรมจากหลายตำแหน่ง กรุณาขยายช่วงวิดีโอ")
        context.update(source_name=source.name, source_profile=source_profile,
                       settings=settings, requested_settings=settings, capture_plan=plan,
                       auto_model=auto_model)
        existing = job_manifest(job)
        label = clean_label(existing.get("label", ""), source.stem)
        if reprocess:
            cleanup_job_derivatives(job)
        manifest = {"label": label, "source_name": source.name,
                    "source_key": source_key, "settings": settings, "source_profile": source_profile,
                    "auto_model": auto_model, "capture_plan": plan}
        save_job_manifest(job, manifest)

        def report(count):
            total = plan.get("total_views")
            save("running", progress=min(99, int(count / total * 100)) if total else 15,
                 step=f"Generated {count}/{total or '?'} perspective views", frames=count)

        report(0)
        def compute_report(metadata):
            context["view_compute"] = metadata
            context["compute_changed_at"] = utc_now()
            context["compute_start_frames"] = 0
        count = process_file(source, directory, progress_callback=report, compute_callback=compute_report, **settings)
        meta = read_json(directory / "transforms.json", {})
        meta["source_key"] = source_key
        meta["job_label"] = label
        write_json(directory / "transforms.json", meta)
        context["settings"] = normalized_settings(meta.get("settings") or settings)
        save("ready", progress=100, step=f"Generated {count} perspective views", frames=count)
        succeeded = True
    except Exception as exc:
        if directory is not None:
            save("error", progress=0, step="View generation failed", error=str(exc))
    finally:
        if succeeded and auto_model:
            # Keep the shared job lock throughout Views -> COLMAP -> GPU training.
            try:
                generate_3d(job)
            except Exception as exc:
                write_json(directory / "3d_status.json", {
                    "state": "error", "stage": "model", "error": str(exc), "step": "failed",
                    **timer.snapshot(finished=True)})
            finally:
                release_task(job)
        else:
            release_task(job)


def start_views(job: str, source_key: str, settings, reprocess=False, auto_model=False):
    if not claim_task(job):
        return False
    thread = threading.Thread(
        target=generate_views, args=(job, source_key, settings, reprocess, auto_model), daemon=True
    )
    thread.start()
    return True


def source_from_route(kind: str, filename: str):
    """Resolve a source-file URL without allowing paths outside its source folder."""
    directory = SOURCE_DIRECTORIES.get(kind)
    if (directory is None or not filename or "/" in filename or "\\" in filename
            or Path(filename).name != filename):
        return None
    if Path(filename).suffix.lower() not in ALLOWED:
        return None
    target = (directory / filename).resolve()
    if target.parent != directory.resolve() or not target.is_file():
        return None
    return target


def source_route(kind: str, source: Path, preview=False) -> str:
    endpoint = "preview" if preview else "source"
    return f"/{endpoint}/{kind}/{quote(source.name, safe='')}"


def model_status(job):
    return read_json(JOBS / job / "3d_status.json", {}) or None


def prepare_brush_dataset(processed_dir: Path, brush_data_dir: Path) -> int:
    """Copy only the Nerfstudio files Brush needs into an unambiguous dataset."""
    transforms_file = processed_dir / "transforms.json"
    metadata = json.loads(transforms_file.read_text(encoding="utf-8"))
    frames = metadata.get("frames", [])
    if len(frames) < 3:
        raise RuntimeError("COLMAP ลงทะเบียนภาพได้น้อยกว่า 3 ภาพ จึงยังสร้าง Gaussian Splat ไม่ได้")

    point_cloud_name = Path(metadata.get("ply_file_path", "sparse_pc.ply")).name
    point_cloud = processed_dir / point_cloud_name
    if not point_cloud.is_file():
        raise RuntimeError(f"ไม่พบ point cloud จาก COLMAP: {point_cloud_name}")

    shutil.rmtree(brush_data_dir, ignore_errors=True)
    brush_images = brush_data_dir / "images"
    brush_images.mkdir(parents=True, exist_ok=True)
    processed_root = processed_dir.resolve()
    for frame in frames:
        relative_path = Path(frame["file_path"])
        source = (processed_dir / relative_path).resolve()
        destination = (brush_data_dir / relative_path).resolve()
        if processed_root not in source.parents or not source.is_file():
            raise RuntimeError(f"ไม่พบภาพที่ COLMAP ลงทะเบียน: {relative_path}")
        if brush_data_dir.resolve() not in destination.parents:
            raise RuntimeError(f"เส้นทางภาพไม่ถูกต้อง: {relative_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Brush's Rust JPEG decoder rejects a few otherwise readable JPEGs
        # produced during image preparation with an "early eof" error. Decode
        # and encode each registered view once so the training set is uniform.
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None or not cv2.imwrite(
            str(destination), image, [cv2.IMWRITE_JPEG_QUALITY, 95]
        ):
            raise RuntimeError(f"Could not prepare registered image for Brush: {relative_path}")
    shutil.copy2(transforms_file, brush_data_dir / "transforms.json")
    shutil.copy2(point_cloud, brush_data_dir / point_cloud_name)
    return len(frames)


def run_colmap_with_known_camera(colmap: Path, ns_process: Path, input_dir: Path,
                                 processed_dir: Path, metadata: dict, matching_method: str,
                                 log, process_env, save, compute_quality=None) -> int:
    """Reconstruct views while keeping the exact virtual-camera intrinsics fixed."""
    database = processed_dir / "colmap" / "database.db"
    sparse_root = processed_dir / "colmap" / "sparse"
    sparse_root.mkdir(parents=True, exist_ok=True)
    params = ",".join(str(metadata[key]) for key in ("fl_x", "fl_y", "cx", "cy"))
    version_probe = subprocess.run(
        [str(colmap), "-h"], capture_output=True, text=True, check=False,
        cwd=ROOT, env=process_env,
    )
    compute = select_colmap_compute(version_probe.stdout + version_probe.stderr, process_env)
    if compute_quality is not None:
        compute_quality.update(compute.metadata())
    log.write(f"COLMAP compute: {compute.backend} ({compute.reason}); GPU index {compute.gpu_index}\n")
    log_options = ["--log_target", "stdout"] if colmap_version(version_probe.stdout + version_probe.stderr) >= (4, 0, 0) else []

    def run(command):
        nonlocal compute
        log.write("$ " + " ".join(map(str, command)) + "\n")
        log.flush()
        compute = run_colmap_command(command, compute, log, process_env, ROOT)

    save("running", progress=8, step=f"COLMAP: extracting {compute.backend} features with fixed pinhole intrinsics")
    run([
        str(colmap), "feature_extractor", "--database_path", str(database),
        "--image_path", str(input_dir), "--ImageReader.camera_model", "PINHOLE",
        "--ImageReader.single_camera", "1", "--ImageReader.camera_params", params,
        *compute.options("extraction"), *log_options,
    ])
    save("running", progress=18, step=f"COLMAP: {matching_method} feature matching on {compute.backend}")
    run([
        str(colmap), f"{matching_method}_matcher", "--database_path", str(database),
        *compute.options("matching"), *log_options,
    ])
    if compute_quality is not None:
        compute_quality.update(compute.metadata())
    save("running", progress=28, step="COLMAP: estimating camera poses")
    run([
        str(colmap), "mapper", "--database_path", str(database),
        "--image_path", str(input_dir), "--output_path", str(sparse_root),
        "--Mapper.ba_refine_focal_length", "0",
        "--Mapper.ba_refine_principal_point", "0",
        "--Mapper.ba_refine_extra_params", "0",
        "--Mapper.min_model_size", "3", *log_options,
    ])

    models = []
    for model in sparse_root.iterdir():
        images_bin = model / "images.bin"
        if model.is_dir() and images_bin.is_file():
            header = images_bin.read_bytes()[:8]
            if len(header) == 8:
                models.append((struct.unpack("<Q", header)[0], model))
    if not models:
        raise RuntimeError("COLMAP did not create a sparse model. The source needs more overlap and camera movement.")
    registered_count, best_model = max(models, key=lambda item: item[0])
    if registered_count < 3:
        raise RuntimeError("COLMAP registered fewer than 3 images. The source does not have enough parallax for 3D.")

    save("running", progress=36, step=f"Preparing {registered_count} registered views for Brush")
    model_relative = best_model.relative_to(processed_dir)
    run([
        str(ns_process), "images", "--data", str(input_dir), "--output-dir", str(processed_dir),
        "--skip-colmap", "--colmap-model-path", str(model_relative),
        "--camera-type", "pinhole", "--no-gpu",
    ])
    return int(registered_count)


def extract_spherical_keyframes(source: Path, metadata: dict, output_dir: Path) -> int:
    """Recreate the sampled equirectangular frames used to make perspective crops."""
    expected = {
        int(frame["frame_index"])
        for frame in metadata.get("frames", [])
        if frame.get("frame_index") is not None
    }
    if len(expected) < 3:
        raise RuntimeError("A moving 360 source needs at least 3 sampled panorama frames")
    sample_every = int(metadata.get("settings", {}).get("sample_every", 1))
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    # The clip can begin between multiples of sample_every. Preserve its exact IDs.
    first, last = min(expected), max(expected)
    for frame_index, panorama in frames_from_input(source, sample_every, first, last + 1):
        if frame_index not in expected:
            continue
        height, width = panorama.shape[:2]
        if width > 1920:
            target_height = max(1, round(height * 1920 / width))
            panorama = cv2.resize(panorama, (1920, target_height), interpolation=cv2.INTER_AREA)
        destination = output_dir / f"pano_{frame_index:06d}.jpg"
        if not cv2.imwrite(str(destination), panorama, [cv2.IMWRITE_JPEG_QUALITY, 94]):
            raise RuntimeError(f"Could not write spherical keyframe {frame_index}")
        written += 1
    if written != len(expected):
        raise RuntimeError(
            f"Could only extract {written}/{len(expected)} panorama keyframes from the source"
        )
    return written


def run_spherical_colmap(colmap: Path, source: Path, view_dir: Path, work_dir: Path,
                         brush_data_dir: Path, metadata: dict, log, process_env, save) -> dict:
    """Estimate one native spherical pose per panorama, then apply known crop yaws."""
    version_probe = subprocess.run(
        [str(colmap), "-h"], capture_output=True, text=True, check=False,
        cwd=ROOT, env=process_env,
    )
    banner = version_probe.stdout + version_probe.stderr
    if colmap_version(banner) < (4, 1, 0):
        raise RuntimeError(
            "360 reconstruction requires COLMAP 4.1 or newer with the EQUIRECTANGULAR camera model. "
            "Run install_colmap.ps1 (Windows) or bash setup.sh (Ubuntu) and restart the app."
        )
    compute = select_colmap_compute(banner, process_env)
    log.write(f"COLMAP compute: {compute.backend} ({compute.reason}); GPU index {compute.gpu_index}\n")

    panorama_dir = work_dir / "panoramas"
    database = work_dir / "database.db"
    sparse_root = work_dir / "sparse"
    shutil.rmtree(work_dir, ignore_errors=True)
    sparse_root.mkdir(parents=True, exist_ok=True)
    save("running", progress=7, step="Extracting spherical keyframes")
    panorama_count = extract_spherical_keyframes(source, metadata, panorama_dir)
    first_panorama = cv2.imread(str(next(iter(sorted(panorama_dir.glob("*.jpg"))))))
    if first_panorama is None:
        raise RuntimeError("Could not read an extracted spherical keyframe")
    mask_height, mask_width = first_panorama.shape[:2]
    camera_mask = np.full((mask_height, mask_width), 255, dtype=np.uint8)
    # The nadir commonly contains the car roof, tripod, operator, or selfie
    # stick.  These move with the camera and create stationary false matches.
    camera_mask[int(mask_height * 0.72):, :] = 0
    camera_mask_path = work_dir / "camera_mask.png"
    if not cv2.imwrite(str(camera_mask_path), camera_mask):
        raise RuntimeError("Could not write the spherical camera-rig mask")

    def run(command):
        nonlocal compute
        log.write("$ " + " ".join(map(str, command)) + "\n")
        log.flush()
        compute = run_colmap_command(command, compute, log, process_env, ROOT)

    common_log = ["--log_target", "stdout"]
    save("running", progress=12, step=f"COLMAP: extracting {compute.backend} features from {panorama_count} spherical frames")
    run([
        str(colmap), "feature_extractor", "--database_path", str(database),
        "--image_path", str(panorama_dir),
        "--ImageReader.camera_model", "EQUIRECTANGULAR",
        "--ImageReader.single_camera", "1",
        "--ImageReader.camera_mask_path", str(camera_mask_path),
        *compute.options("extraction"),
        "--FeatureExtraction.num_threads", str(max(1, min(8, os.cpu_count() or 1))),
        "--SiftExtraction.max_num_features", "12000", *common_log,
    ])
    sequential = panorama_count >= 60
    matcher = "sequential_matcher" if sequential else "exhaustive_matcher"
    save("running", progress=21, step=f"COLMAP: {matcher.replace('_', ' ')} on {compute.backend}")
    matcher_command = [
        str(colmap), matcher, "--database_path", str(database),
        *compute.options("matching"),
    ]
    if sequential:
        matcher_command.extend(["--SequentialMatching.overlap", "10"])
    run([*matcher_command, *common_log])
    save("running", progress=29, step="COLMAP: estimating one pose per panorama")
    mapper_command = [
        str(colmap), "mapper", "--database_path", str(database),
        "--image_path", str(panorama_dir), "--output_path", str(sparse_root),
        "--Mapper.min_model_size", "3", *common_log,
    ]
    expanded_matches = False
    try:
        run(mapper_command)
    except subprocess.CalledProcessError:
        if not sequential:
            raise
        # Some paths revisit older frames or have uneven capture spacing.
        # Add exhaustive pairs and retry without repeating feature extraction.
        save("running", progress=25, step="COLMAP: expanding spherical matches")
        run([
            str(colmap), "exhaustive_matcher", "--database_path", str(database),
            *compute.options("matching"), *common_log,
        ])
        expanded_matches = True
        shutil.rmtree(sparse_root, ignore_errors=True)
        sparse_root.mkdir(parents=True, exist_ok=True)
        save("running", progress=29, step="COLMAP: retrying spherical camera poses")
        run(mapper_command)

    def collect_models():
        found = []
        for model in sparse_root.iterdir():
            images_bin = model / "images.bin"
            if model.is_dir() and images_bin.is_file():
                header = images_bin.read_bytes()[:8]
                if len(header) == 8:
                    found.append((struct.unpack("<Q", header)[0], model))
        return found

    models = collect_models()
    required_for_expansion = max(6, int(np.ceil(panorama_count * 0.6)))
    if (sequential and models and max(models, key=lambda item: item[0])[0] < required_for_expansion
            and not expanded_matches):
        save("running", progress=25, step="COLMAP: expanding sparse sequence matches")
        run([
            str(colmap), "exhaustive_matcher", "--database_path", str(database),
            *compute.options("matching"), *common_log,
        ])
        expanded_matches = True
        shutil.rmtree(sparse_root, ignore_errors=True)
        sparse_root.mkdir(parents=True, exist_ok=True)
        save("running", progress=29, step="COLMAP: retrying expanded camera poses")
        run(mapper_command)
        models = collect_models()
    if not models:
        raise RuntimeError(
            "Spherical COLMAP could not recover a camera path. Use a stitched 2:1 video "
            "with a moving camera, a static scene, and clear visual overlap."
        )
    registered_count, best_model = max(models, key=lambda item: item[0])
    required = max(6, int(np.ceil(panorama_count * 0.5)))
    if registered_count < required:
        raise RuntimeError(
            f"Quality check failed: spherical COLMAP registered only "
            f"{registered_count}/{panorama_count} panoramas; at least {required} are required."
        )

    save("running", progress=37, step=f"Building {registered_count}-pose spherical Brush dataset")
    result = build_brush_dataset(view_dir, best_model, brush_data_dir)
    if result["max_same_frame_center_spread"] > 1e-8:
        raise RuntimeError("Quality check failed: crops from one panorama do not share a camera center")
    if result["sparse_points"] < 1000:
        raise RuntimeError(
            f"Quality check failed: only {result['sparse_points']} stable 3D points were recovered"
        )
    if result["path_to_scene_ratio"] < 0.02:
        raise RuntimeError(
            "Quality check failed: the camera stayed too close to one position. "
            "Walk around the subject to create parallax; rotating in place cannot recover 3D depth."
        )
    if result["mean_reprojection_error"] > 2.5:
        raise RuntimeError(
            f"Quality check failed: reprojection error is "
            f"{result['mean_reprojection_error']:.2f}px (limit 2.5px)"
        )
    result["input_panoramas"] = panorama_count
    result.update(compute.metadata())
    return result


def sanitize_splat_ply(source: Path, destination: Path) -> dict:
    """Validate numerical values without discarding learned scene geometry.

    Long, thin Gaussians and distant centers can represent real surfaces. A
    radius or aspect-ratio filter degraded held-out renders of published PLYs,
    so geometry pruning must be evaluated separately before it is applied.
    """
    with source.open("rb") as handle:
        header_lines = []
        while True:
            line = handle.readline()
            if not line:
                raise RuntimeError("Brush exported an invalid PLY without end_header")
            header_lines.append(line)
            if line.strip() == b"end_header":
                break
        payload = handle.read()
    header = b"".join(header_lines).decode("ascii")
    if "format binary_little_endian 1.0" not in header:
        raise RuntimeError("Brush exported an unsupported PLY format")
    count_match = re.search(r"element vertex (\d+)", header)
    property_names = re.findall(r"property float (\S+)", header)
    required = {"x", "y", "z", "opacity", "scale_0", "scale_1", "scale_2"}
    if not count_match or not required.issubset(property_names):
        raise RuntimeError("Brush PLY is missing Gaussian splat properties")
    count = int(count_match.group(1))
    dtype = np.dtype([(name, "<f4") for name in property_names])
    values = np.frombuffer(payload, dtype=dtype, count=count)
    if len(values) != count:
        raise RuntimeError("Brush exported a truncated PLY")

    matrix = values.view("<f4").reshape(count, len(property_names))
    positions = np.column_stack([values[name] for name in ("x", "y", "z")])
    log_scales = np.column_stack([values[f"scale_{index}"] for index in range(3)])
    keep = np.isfinite(matrix).all(axis=1) & (log_scales <= 80).all(axis=1)
    if {'rot_0','rot_1','rot_2','rot_3'}.issubset(property_names):
        rotation = np.column_stack([values[f'rot_{i}'] for i in range(4)]).astype(np.float64)
        keep &= np.linalg.norm(rotation, axis=1) > 1e-12
    kept = values[keep]
    if len(kept) < max(1000, int(count * 0.5)):
        raise RuntimeError(
            f"Model quality check rejected too many splats ({len(kept)}/{count}); "
            "the recovered scene geometry is unstable"
        )
    finite_positions = positions[keep].astype(np.float64)
    center = np.median(finite_positions, axis=0)
    scene_radius = max(float(np.percentile(np.linalg.norm(finite_positions-center, axis=1),90)),1e-6)
    clean_header = re.sub(
        rb"element vertex \d+", f"element vertex {len(kept)}".encode("ascii"),
        b"".join(header_lines), count=1,
    )
    with destination.open("wb") as handle:
        handle.write(clean_header)
        handle.write(kept.tobytes())
    return {
        "raw_splats": count,
        "published_splats": len(kept),
        "removed_outlier_splats": count - len(kept),
        "scene_radius_p90": round(scene_radius, 6),
        "cleanup_policy": "numerical_validation_only; learned geometry preserved",
    }


def evaluate_brush_renders(dataset_dir: Path, export_dir: Path, step=10000) -> dict:
    """Measure held-out render quality, respecting Brush's dynamic masks."""
    render_dir = export_dir / f"eval_{step}"
    metadata = read_json(dataset_dir / 'transforms.json', {})
    inputs = {Path(frame.get('file_path', '')).stem: dataset_dir / frame.get('file_path', '')
              for frame in metadata.get('frames', [])}
    psnr_values = []
    for rendered_path in render_dir.glob("*.png"):
        stem = rendered_path.stem.removesuffix(".jpg").removesuffix(".jpeg")
        image_path = inputs.get(stem, dataset_dir / "images" / f"{stem}.jpg")
        if not image_path.is_file():
            image_path = dataset_dir / "images" / rendered_path.stem
        target = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        rendered = cv2.imread(str(rendered_path), cv2.IMREAD_COLOR)
        if target is None or rendered is None:
            continue
        if rendered.shape[:2] != target.shape[:2]:
            rendered = cv2.resize(rendered, (target.shape[1], target.shape[0]))
        squared_error = np.mean(
            (rendered.astype(np.float32) - target.astype(np.float32)) ** 2, axis=2
        )
        mask_path = dataset_dir / "masks" / f"{stem}.png"
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        valid = mask > 127 if mask is not None else np.ones(target.shape[:2], dtype=bool)
        if np.any(valid):
            mse = float(np.mean(squared_error[valid]))
            psnr_values.append(99.0 if mse == 0 else 10.0 * np.log10(255.0 ** 2 / mse))
    if not psnr_values:
        return {"eval_views": 0}
    return {
        "eval_views": len(psnr_values),
        "eval_psnr_mean_db": round(float(np.mean(psnr_values)), 3),
        "eval_psnr_median_db": round(float(np.median(psnr_values)), 3),
    }


def brush_runtime_environment(process_env: dict) -> dict:
    """Log the selected adapter without verbose dataset image dumps."""
    environment = process_env.copy()
    filters = environment.get("RUST_LOG") or "warn"
    environment["RUST_LOG"] = filters + ",cubecl_wgpu::runtime=info,brush_cli=info"
    return environment


def read_brush_adapter(log_file: Path) -> dict:
    """Read the device actually selected by Brush, rather than a GPU inventory."""
    if not log_file.is_file():
        return {}
    adapter = {}
    with log_file.open(encoding="utf-8", errors="replace") as log:
        for line in log:
            if "Using adapter AdapterInfo" not in line:
                continue
            name = re.search(r'name: "([^"]+)"', line)
            backend = re.search(r"backend: (\w+)", line)
            device_type = re.search(r"device_type: (\w+)", line)
            if name and backend and device_type:
                adapter = {
                    "compute_device": name.group(1),
                    "compute_backend": backend.group(1),
                    "compute_device_type": device_type.group(1),
                }
    return adapter


def cached_pose_preview(job_dir: Path) -> dict:
    preview = read_json(job_dir / "pose_preview.json", {})
    dataset = job_dir / "brush_data"
    pose_state = read_json(job_dir / "pose_status.json", {}).get("state")
    model_state = read_json(job_dir / "3d_status.json", {}).get("state")
    if (not preview or not (dataset / "transforms.json").is_file()
            or not (dataset / "sparse_pc.ply").is_file()
            or (pose_state != "ready" and model_state != "done")):
        return {}
    return preview if preview.get("input_signature") == pose_input_signature(job_dir) else {}


def available_pose_preview(job: str) -> dict:
    directory = job_directory(job)
    if directory is None or not (directory / "transforms.json").is_file():
        return {}
    preview = read_json(directory / "pose_preview.json", {})
    if preview and preview.get("input_signature") == pose_input_signature(directory):
        return preview
    if task_is_active(job) or not (directory / "brush_data" / "transforms.json").is_file():
        return {}
    # Adopt datasets from jobs created before the separate preview stage existed.
    quality = (model_status(job) or {}).get("quality", {})
    return build_pose_preview(directory, quality)


def recover_training_jpegs(dataset_dir):
    """Preserve decoded pixels with PNG when Brush's JPEG reader reports EOF."""
    metadata_path = dataset_dir / 'transforms.json'
    metadata = read_json(metadata_path, {})
    converted = 0
    for frame in metadata.get('frames', []):
        relative = Path(frame.get('file_path', ''))
        if relative.suffix.lower() not in {'.jpg', '.jpeg'}:
            continue
        source = (dataset_dir / relative).resolve()
        if not source.is_relative_to(dataset_dir.resolve()) or not source.is_file():
            raise RuntimeError('พาธภาพเทรนไม่ถูกต้อง ขณะกู้คืนตัวอ่าน JPEG')
        target = source.with_suffix('.png')
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None or not cv2.imwrite(str(target), image):
            raise RuntimeError(f'กู้คืนภาพเทรนไม่ได้: {relative.name}')
        frame['file_path'] = target.relative_to(dataset_dir.resolve()).as_posix()
        converted += 1
    if converted:
        write_json(metadata_path, metadata)
    return converted


def generate_3d(job, poses_only=False, identifier=None, selected_frames=None, selection_signature=None):
    """Run COLMAP pose estimation, train with Brush, and export a PLY."""
    job_dir = job_directory(job)
    if job_dir is None:
        release_task(job)
        raise ValueError("ไม่พบ job")
    timer = TaskTimer()
    phase_timer = PhaseTimer()
    if not poses_only:
        identifier = identifier or allocate_model_run(job, selected_frames, selection_signature)
    run_dir = job_dir / "model_runs" / identifier if identifier else job_dir
    status_file = job_dir / ("pose_status.json" if poses_only else "3d_status.json")
    log_file = job_dir / "poses.log" if poses_only else run_dir / "3d.log"
    ns_process = nerfstudio_cli("ns-process-data")
    brush = brush_cli()
    processed_dir = job_dir / "ns_data"
    brush_data_dir = job_dir / "brush_data"
    export_dir = run_dir / "brush_export"
    input_dir = job_dir / "_input_images"
    attempt_log_started = False
    quality = {}
    run_metadata = read_json(run_dir / "status.json", {}) if not poses_only else {}
    def save(state, **extra):
        status = {**run_metadata, "state": state, "stage": "poses" if poses_only else "model",
                  **timer.snapshot(finished=state in {"ready", "done", "error"}), **extra}
        status = phase_timer.update(status)
        if not poses_only:
            write_json(run_dir / "status.json", status)
        write_json(status_file, status)
        return status
    try:
        preview = cached_pose_preview(job_dir)
        reused_preview = bool(preview)
        if selected_frames is not None:
            selected_frames = validate_selection(preview, selected_frames, selection_signature)
        if not poses_only and not brush.is_file():
            raise RuntimeError(
                "ไม่พบ Brush trainer: รัน install_brush.ps1 (Windows) หรือ bash setup.sh (Ubuntu); "
                "Brush ใช้ WGPU จึงไม่ต้องติดตั้ง HIP หรือ CUDA"
            )
        manifest = job_manifest(job)
        quality_name = manifest.get("settings", {}).get("quality", "high")
        training_profile = QUALITY_PROFILES.get(quality_name, QUALITY_PROFILES["high"])
        training_steps = int(training_profile["steps"])
        spherical_source = None
        # Read source mode before selecting COLMAP because native panorama
        # reconstruction requires the EQUIRECTANGULAR model from COLMAP 4.1+.
        if manifest.get("source_mode") == "panorama":
            spherical_source = source_from_key(manifest.get("source_key", ""))
            if spherical_source is None:
                raise RuntimeError("The original 360 source is required to recover spherical camera poses")
            recorded_source = manifest.get("source_profile")
            if recorded_source:
                current_source = probe_source(spherical_source)
                if any(recorded_source.get(key) != current_source.get(key) for key in ("fingerprint", "modified_ns")):
                    raise RuntimeError("ไฟล์ต้นฉบับเปลี่ยนหลังสร้าง Views กรุณาสร้าง Views ใหม่ก่อนหา pose หรือ train")
        colmap_bin = colmap_bin_dir(prefer_modern=bool(spherical_source))
        if colmap_bin is None and not preview:
            raise RuntimeError("ไม่พบ COLMAP: รัน install_colmap.ps1 (Windows) หรือ bash setup.sh (Ubuntu)")
        save("running", progress=5, step="COLMAP: preparing camera poses")
        process_env = os.environ.copy()
        process_env["PYTHONUTF8"] = "1"
        process_env["PYTHONIOENCODING"] = "utf-8"
        process_env["QT_QPA_PLATFORM"] = "offscreen"
        if colmap_bin:
            process_env["PATH"] = str(colmap_bin) + os.pathsep + process_env.get("PATH", "")
        qt_plugins = colmap_bin.parent / "plugins" if colmap_bin else ROOT / "tools" / "colmap" / "plugins"
        if qt_plugins.is_dir():
            process_env["QT_PLUGIN_PATH"] = str(qt_plugins) + os.pathsep + process_env.get("QT_PLUGIN_PATH", "")
        if not preview and shutil.which("ffmpeg", path=process_env.get("PATH", "")) is None:
            raise RuntimeError("ไม่พบ FFmpeg ใน PATH; ติดตั้งด้วย winget install Gyan.FFmpeg.Essentials (Windows) หรือ sudo apt install ffmpeg (Ubuntu)")
        process_env.pop("DISPLAY", None)
        input_images = sorted(job_dir.glob("frame_*.jpg"))
        if len(input_images) < 2:
            raise RuntimeError("ต้องมีภาพ perspective อย่างน้อย 2 ภาพสำหรับสร้าง 3D")
        metadata = json.loads((job_dir / "transforms.json").read_text(encoding="utf-8"))
        frame_ids = {frame.get("frame_index") for frame in metadata.get("frames", []) if frame.get("frame_index") is not None}
        if len(frame_ids) == 1:
            raise RuntimeError(
                "ชุดนี้มาจากภาพ 360 เพียงเฟรมเดียว: การแตกเป็นหลายมุมยังได้ตำแหน่งกล้องเดียวกัน "
                "จึงไม่มี parallax ให้สร้าง geometry 3D กรุณาใช้ equirectangular video หรือชุดภาพที่ถ่ายขณะเคลื่อนกล้องรอบฉาก"
            )
        if not preview:
            shutil.rmtree(input_dir, ignore_errors=True)
            shutil.rmtree(processed_dir, ignore_errors=True)
            input_dir.mkdir(parents=True, exist_ok=True)
            for image in input_images:
                shutil.copy2(image, input_dir / image.name)
        # Keep exact intrinsics for the virtual perspective cameras. Allowing
        # COLMAP to refine these values can turn a 90-degree view into a very
        # long focal length and prevent registration beyond the initial pair.
        matching_method = "sequential" if len(input_images) > 400 else "exhaustive"
        colmap = colmap_bin / ("colmap.exe" if os.name == "nt" else "colmap") if colmap_bin else None
        if not poses_only:
            shutil.rmtree(export_dir, ignore_errors=True)
            export_dir.mkdir(parents=True, exist_ok=True)
        with log_file.open("w", encoding="utf-8") as log:
            attempt_log_started = True
            quality = {}
            if preview:
                quality = dict(preview.get("quality", {}))
                registered_count = len(preview["views"])
                log.write(f"Reusing previewed camera poses: {registered_count} views.\n")
            elif spherical_source:
                quality = run_spherical_colmap(
                    colmap, spherical_source, job_dir, job_dir / "spherical_colmap",
                    brush_data_dir, metadata, log, process_env, save,
                )
                registered_count = int(quality["perspective_views"])
                log.write(
                    "Spherical quality: "
                    f"{quality['registered_panoramas']}/{quality['input_panoramas']} panoramas, "
                    f"{registered_count} perspective views, {quality['sparse_points']} points, "
                    f"same-frame center spread={quality['max_same_frame_center_spread']:.3g}.\n"
                )
            else:
                registered_count = run_colmap_with_known_camera(
                    colmap, ns_process, input_dir, processed_dir, metadata,
                    matching_method, log, process_env, save,
                    compute_quality=quality,
                )
                prepared_count = prepare_brush_dataset(processed_dir, brush_data_dir)
                if prepared_count != registered_count:
                    registered_count = prepared_count
                quality.update(registered_views=registered_count, input_views=len(input_images))
                if registered_count < max(10, len(input_images) // 5):
                    raise RuntimeError(
                        f"Quality check failed: COLMAP registered only "
                        f"{registered_count}/{len(input_images)} views"
                    )
            if not preview:
                preview = build_pose_preview(job_dir, quality)
            pose_result = {
                "state": "ready", "stage": "poses", "progress": 100,
                "step": "Camera poses ready", "quality": quality,
                "summary": preview["summary"], "warnings": preview["warnings"],
            }
            if poses_only:
                save("ready", **{key: value for key, value in pose_result.items()
                                 if key not in {"state", "stage"}})
                return
            if not reused_preview:
                write_json(job_dir / "pose_status.json", {
                    **pose_result, **timer.snapshot(), "finished_at": utc_now(),
                })
            elif not (job_dir / "pose_status.json").is_file():
                write_json(job_dir / "pose_status.json", pose_result)
            if spherical_source and (not (brush_data_dir / "masks").is_dir()
                                     or not quality.get("mask_model")):
                save("running", progress=39, step="Masking people and the camera operator")
                def mask_progress(done, total, compute):
                    quality['mask_compute'] = compute
                    save('running', progress=39, step=f'Masking people {done:,}/{total:,} · {compute["backend"]}', quality=dict(quality))
                quality.update(create_dynamic_person_masks(brush_data_dir, progress_callback=mask_progress))
                preview["quality"] = quality
                write_json(job_dir / "pose_preview.json", preview)
            training_data_dir = brush_data_dir
            automatic_seed_filter = selected_frames is None and manifest.get("auto_model") and spherical_source
            if automatic_seed_filter:
                selected_frames = [camera["frame_index"] for camera in preview["cameras"]]
            if selected_frames is not None:
                save("running", progress=39, step=("Filtering sparse seed points against camera tracks and masks"
                     if automatic_seed_filter else "Preparing selected camera positions and supported seed points"))
                training_data_dir = run_dir / "dataset"
                selection = build_training_subset(job_dir, training_data_dir, selected_frames, preview)
                selection["mode"] = "all_registered_with_seed_filter" if automatic_seed_filter else "selected_positions"
                write_json(run_dir / "selection.json", selection)
                run_metadata["selection"] = selection
                quality.update(selected_positions=selection["positions"], selected_views=selection["views"],
                               selected_sparse_points=selection["sparse_points"],
                               seed_filter=selection["seed_filter"],
                               original_sparse_points=selection["original_sparse_points"])
                registered_count = selection["views"]
                log.write("Selected dataset: " + json.dumps(selection, ensure_ascii=False) + "\n")
            else:
                # Preserve the exact input of every model version across reprocessing.
                training_data_dir = run_dir / "dataset"
                def link_or_copy(source, target):
                    try:
                        os.link(source, target)
                    except OSError:
                        shutil.copy2(source, target)
                    return target
                shutil.copytree(brush_data_dir, training_data_dir, copy_function=link_or_copy)
            train_command = [
                str(brush), str(training_data_dir), "--total-steps", str(training_steps),
                "--max-resolution", str(training_profile["max_resolution"]),
                "--max-splats", str(training_profile["max_splats"]),
                "--growth-stop-iter", str(max(1, int(training_steps * .75))),
                # 17 is coprime with the 16-view High panorama layout, so
                # held-out renders cover different yaw and pitch directions.
                "--eval-split-every", "17", "--eval-every", str(training_steps),
                "--eval-save-to-disk",
                "--export-every", str(training_steps), "--export-path", str(export_dir),
                "--export-name", "splat.ply",
            ]
            save(
                "running", progress=40,
                step=(f"Training {registered_count} views with Brush/WGPU "
                      f"({quality_name}, {training_steps:,} steps)"),
                training_steps=training_steps, training_views=registered_count,
            )
            log.write("$ " + " ".join(train_command) + "\n")
            log.flush()
            try:
                subprocess.run(train_command, stdout=log, stderr=subprocess.STDOUT, check=True,
                               cwd=ROOT, env=brush_runtime_environment(process_env))
            except subprocess.CalledProcessError:
                log.flush()
                tail = log_file.read_text(encoding='utf-8', errors='replace')[-8192:]
                if 'Failed to load format' not in tail or 'early eof' not in tail:
                    raise
                count = recover_training_jpegs(training_data_dir)
                if not count:
                    raise
                quality['image_codec_recovery'] = 'JPEG decoder EOF; lossless PNG of decoded input pixels'
                quality['reencoded_training_images'] = count
                save('running', progress=40, step=f'กู้คืนตัวอ่าน JPEG ด้วย PNG ({count} ภาพ) แล้วเริ่ม train ใหม่',
                     training_steps=training_steps, training_views=registered_count)
                log.write(f'JPEG decoder EOF recovery: converted {count} training images to PNG. Retrying once.\n')
                log.flush()
                subprocess.run(train_command, stdout=log, stderr=subprocess.STDOUT, check=True,
                               cwd=ROOT, env=brush_runtime_environment(process_env))
        quality.update(read_brush_adapter(log_file))
        save("running", progress=95, step="Evaluating renders and publishing the model",
             training_steps=training_steps, training_views=registered_count)
        splat_file = export_dir / "splat.ply"
        if not splat_file.is_file():
            raise RuntimeError(f"Brush train จบแล้วแต่ไม่พบไฟล์ PLY ที่คาดไว้: {splat_file}")
        quality["quality_preset"] = quality_name
        quality["training_steps"] = training_steps
        quality["max_splats"] = int(training_profile["max_splats"])
        quality.update(evaluate_brush_renders(training_data_dir, export_dir, step=training_steps))
        if manifest.get("auto_model") and quality.get("eval_views", 0) < 3:
            raise RuntimeError("ยังไม่มีภาพ Render สำหรับตรวจคุณภาพเพียงพอ กรุณาใช้ช่วงคลิปที่ยาวขึ้น")
        if quality.get("eval_psnr_mean_db", 99.0) < 18.0:
            raise RuntimeError(
                f"Model quality check failed: held-out PSNR is "
                f"{quality['eval_psnr_mean_db']:.2f} dB (minimum 18 dB)"
            )
        model_name = f"{job}_{identifier}.ply"
        validated_file = export_dir / "validated.ply"
        splat_quality = sanitize_splat_ply(splat_file, validated_file)
        quality.update(splat_quality)
        validated_file.replace(MODELS / model_name)
        status = save("done", progress=100, step="complete", model=model_name, quality=quality)
        write_json(model_metadata_path(model_name), {
            **status, "viewer_config": dataset_viewer_config(job_dir, training_data_dir),
            "view_run_id": views_status(job).get("run_id"),
            "input_signature": pose_input_signature(job_dir),
        })
        start_fast_variant(MODELS / model_name)
    except Exception as exc:
        detail = str(exc)
        if attempt_log_started and log_file.exists():
            quality.update(read_brush_adapter(log_file))
            tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            if tail:
                detail += "\n\n" + "\n".join(tail)
        if "No good initial image pair found" in detail or "failed to create sparse model" in detail:
            detail = (
                "COLMAP หา camera poses ไม่ได้: ภาพต้องมีจุดที่เห็นซ้ำกันและมี parallax "
                "จากการเคลื่อนกล้องรอบฉาก ลองใช้วิดีโอที่เดินหรือโคจรรอบวัตถุ แล้วเว้นระยะการเคลื่อนที่ให้เห็นความลึก\n\n"
                + detail
            )
            capture = manifest.get('capture_plan', {})
            duration = manifest.get('source_profile', {}).get('duration_seconds') or 0
            start = capture.get('start_seconds',0)
            end = capture.get('end_seconds',0)
            if 0 < end-start < duration*.8:
                detail = f'ช่วงที่เลือก {start:g}–{end:g} วินาทีจากทั้งหมด {duration:.2f} วินาทียังหา pose เริ่มต้นไม่ได้ ลองใช้เต็มคลิปหรือช่วงที่กล้องเคลื่อนชัดขึ้นจากปุ่มปรับช่วง / สร้าง Views ใหม่\n\n'+detail
        save("error", progress=0, step="failed", error=detail, quality=quality)
    finally:
        if not poses_only and log_file.is_file():
            try:
                shutil.copy2(log_file, job_dir / "3d.log")
            except OSError:
                pass
        release_task(job)


def start_3d(job, poses_only=False, selected_frames=None, selection_signature=None):
    if not claim_task(job):
        return False
    try:
        identifier = None if poses_only else allocate_model_run(job, selected_frames, selection_signature)
        thread = threading.Thread(target=generate_3d, args=(job, poses_only, identifier, selected_frames, selection_signature), daemon=True)
        thread.start()
    except Exception:
        release_task(job)
        raise
    return True


def refinement_input(model):
    """Resolve the immutable input and raw checkpoint of one model version."""
    if not model or Path(model).name != model or not (MODELS / model).is_file():
        raise ValueError('ไม่พบ Model ที่ต้องการปรับละเอียด')
    info = model_info(MODELS / model)
    job, parent_run = info.get('job'), info.get('run_id', '')
    directory = job_directory(job or '')
    if directory is None or not re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{8}', parent_run):
        raise ValueError('รุ่นนี้ไม่มี Job และ checkpoint สำหรับเทรนต่อ กรุณาสร้าง Model จาก Source อีกครั้ง')
    parent_dir = directory / 'model_runs' / parent_run
    dataset = parent_dir / 'dataset'
    if not (dataset / 'transforms.json').is_file():
        raise ValueError('รุ่นนี้ไม่มีชุดภาพเทรนที่เก็บไว้ กรุณาสร้าง Model ใหม่ก่อนปรับละเอียด')
    checkpoint = parent_dir / 'brush_export' / 'splat.ply'
    if not checkpoint.is_file():
        raise ValueError('ไม่พบ raw checkpoint ของรุ่นนี้สำหรับเทรนต่อ')
    return info, dataset, checkpoint


def refinement_plan(info, dataset):
    metadata = read_json(dataset / 'transforms.json', {})
    total_views = len(metadata.get('frames', []))
    training_views = total_views - (total_views + 16) // 17
    # Large captures need more visits per image, not the same budget as ten positions.
    steps = min(20000, max(10000, training_views * 10))
    return {'steps': steps, 'training_views': training_views,
            'max_resolution': max(int(metadata.get('w', 0)), int(metadata.get('h', 0)), 960),
            'max_splats': max(2000000, int(info.get('quality', {}).get('max_splats', 2000000))),
            'options': {'lr-mean': 1e-6, 'lr-mean-end': 1e-7,
                        'lr-scale': .003, 'lr-scale-end': .0007,
                        'lr-coeffs-dc': .001, 'mean-noise-weight': 2,
                        'scale-loss-weight': 3e-8, 'aux-loss-time': 1,
                        'growth-grad-threshold': .00006, 'growth-select-fraction': .05,
                        'refine-every': 200}}


def refine_model(model, identifier, job=None):
    """Continue one checkpoint in a new run, preserving every parent artifact."""
    # The caller already resolved the job before claiming its task lock. Resolve
    # the checkpoint inside the protected block so a missing file releases it.
    job = job or model_info(MODELS / model).get('job')
    run_dir = JOBS / job / 'model_runs' / identifier
    timer = TaskTimer()
    phase_timer = PhaseTimer()
    run_metadata = read_json(run_dir / 'status.json', {})
    quality = {}
    log_file = run_dir / '3d.log'
    def save(state, **extra):
        status = {**run_metadata, 'state': state, 'stage': 'model',
                  **timer.snapshot(finished=state in {'done', 'error'}), **extra}
        status = phase_timer.update(status)
        write_json(run_dir / 'status.json', status)
        write_json(JOBS / job / '3d_status.json', status)
        return status
    try:
        info, parent_dataset, checkpoint = refinement_input(model)
        if info['job'] != job:
            raise ValueError('Model source changed before refinement started')
        quality = dict(info.get('quality', {}))
        plan = refinement_plan(info, parent_dataset)
        steps = plan['steps']
        parent_steps = int(quality.get('cumulative_training_steps', quality.get('training_steps', 0)))
        # Retain the parent's source and Views identity even if the job was reprocessed.
        for key in ('source_key', 'source_name', 'source_profile', 'view_run_id', 'input_signature',
                    'settings', 'view_settings', 'selection', 'label'):
            if key in info:
                run_metadata[key] = info[key]
        run_metadata.update(refined_from=model, parent_run_id=info['run_id'], refinement_plan=plan,
                            refinement_note='Checkpoint weights reused; optimizer and schedule restart.')
        save('running', progress=5, step='เตรียมเทรนต่อจาก checkpoint เดิม')
        dataset = run_dir / 'dataset'
        def link_or_copy(source, target):
            # JSON files must be independent; pixels and masks remain immutable.
            if Path(source).suffix.lower() == '.json':
                return shutil.copy2(source, target)
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
            return target
        shutil.copytree(parent_dataset, dataset, copy_function=link_or_copy)
        link_or_copy(checkpoint, dataset / 'refinement_init.ply')
        metadata = read_json(dataset / 'transforms.json')
        metadata['ply_file_path'] = 'refinement_init.ply'
        write_json(dataset / 'transforms.json', metadata)
        export = run_dir / 'brush_export'
        export.mkdir()
        command = [str(brush_cli()), str(dataset), '--total-steps', str(steps),
                   '--max-resolution', str(plan['max_resolution']), '--max-splats', str(plan['max_splats']),
                   '--growth-stop-iter', str(steps // 2), '--eval-split-every', '17',
                   '--eval-every', str(steps), '--eval-save-to-disk', '--export-every', str(steps),
                   '--export-path', str(export), '--export-name', 'splat.ply']
        for key, value in plan['options'].items():
            command.extend(['--' + key, str(value)])
        save('running', progress=40, step=f'ปรับละเอียดจาก Model เดิม · เพิ่ม {steps:,} steps',
             training_steps=steps, training_views=plan['training_views'])
        with log_file.open('w', encoding='utf-8') as log:
            log.write('Fine tuning ' + model + '\n' + run_metadata['refinement_note'] + '\n')
            log.write('$ ' + ' '.join(command) + '\n')
            log.flush()
            environment = brush_runtime_environment(os.environ.copy())
            try:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, cwd=ROOT, env=environment)
            except subprocess.CalledProcessError:
                log.flush()
                tail = log_file.read_text(encoding='utf-8', errors='replace')[-8192:]
                if 'Failed to load format' not in tail or 'early eof' not in tail:
                    raise
                count = recover_training_jpegs(dataset)
                if not count:
                    raise
                quality.update(image_codec_recovery='JPEG decoder EOF; lossless PNG of decoded input pixels',
                               reencoded_training_images=count)
                log.write(f'JPEG decoder recovery: {count} PNGs. Retrying once.\n')
                log.flush()
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, cwd=ROOT, env=environment)
        quality.update(read_brush_adapter(log_file))
        save('running', progress=95, step='Evaluating renders and publishing the model',
             training_steps=steps, training_views=plan['training_views'])
        quality.update(training_steps=steps, cumulative_training_steps=parent_steps + steps,
                       refinement_preset='stable_detail', max_splats=plan['max_splats'])
        quality.update(evaluate_brush_renders(dataset, export, steps))
        if quality.get('eval_views', 0) < 3 or quality.get('eval_psnr_mean_db', 0) < 18:
            raise RuntimeError('ผลปรับละเอียดยังไม่มี Render ที่ผ่านเกณฑ์สำหรับตรวจสอบ')
        quality['parent_eval_psnr_mean_db'] = info.get('quality', {}).get('eval_psnr_mean_db')
        quality['eval_psnr_delta_db'] = round(quality['eval_psnr_mean_db'] - (quality['parent_eval_psnr_mean_db'] or 0), 3)
        quality.update(sanitize_splat_ply(export / 'splat.ply', export / 'validated.ply'))
        name = f'{job}_{identifier}.ply'
        (export / 'validated.ply').replace(MODELS / name)
        status = save('done', progress=100, step='ปรับละเอียดเสร็จ · เปิดภาพเทียบเพื่อตรวจผล', model=name, quality=quality)
        write_json(model_metadata_path(name), {**status, 'viewer_config': dataset_viewer_config(JOBS / job, dataset)})
        start_fast_variant(MODELS / name)
    except Exception as exc:
        detail = str(exc)
        if log_file.is_file():
            quality.update(read_brush_adapter(log_file))
            detail += '\n\n' + '\n'.join(log_file.read_text(encoding='utf-8', errors='replace').splitlines()[-12:])
        save('error', progress=0, step='ปรับละเอียดไม่สำเร็จ', error=detail, quality=quality)
    finally:
        release_task(job)


def start_refinement(model):
    info, _, _ = refinement_input(model)
    job = info['job']
    if not claim_task(job):
        return False
    try:
        identifier = allocate_model_run(job)
        threading.Thread(target=refine_model, args=(model, identifier, job), daemon=True).start()
    except Exception:
        release_task(job)
        raise
    return True


def nerfstudio_cli(name):
    """Resolve a Nerfstudio command from the same environment running this app."""
    suffix = ".exe" if os.name == "nt" else ""
    found = shutil.which(name)
    if found:
        return Path(found)
    scripts_dir = Path(sysconfig.get_path("scripts"))
    return scripts_dir / f"{name}{suffix}"


def colmap_bin_dir(prefer_modern=False):
    """Find the COLMAP bin folder and make its executable discoverable to Nerfstudio."""
    executable = "colmap.exe" if os.name == "nt" else "colmap"
    override = os.environ.get("COLMAP_BIN")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            path = path.parent
        if (path / executable).is_file():
            return path
    bundled_versions = (
        ROOT / "tools" / "colmap" / "bin",
        ROOT / "tools" / "colmap-3.12.4" / "bin",
    ) if prefer_modern else (
        ROOT / "tools" / "colmap-3.12.4" / "bin",
        ROOT / "tools" / "colmap" / "bin",
    )
    for bundled in bundled_versions:
        if (bundled / executable).is_file():
            return bundled
    found = shutil.which("colmap")
    return Path(found).parent if found else None


def brush_cli():
    """Resolve Brush from an override, the project tools folder, or PATH."""
    override = os.environ.get("BRUSH_CLI")
    if override:
        return Path(override).expanduser()
    name = "brush_app.exe" if os.name == "nt" else "brush_app"
    bundled = ROOT / "tools" / "brush" / name
    if bundled.is_file():
        return bundled
    found = shutil.which("brush_app") or shutil.which("brush")
    return Path(found) if found else bundled

APP_STYLE = """
.resource-preview{position:relative;display:block;flex:0 0 144px;width:144px;aspect-ratio:16/9;overflow:hidden;border:1px solid var(--line);border-radius:9px;background:#070b13}.resource-preview:hover,.resource-preview:focus-visible{border-color:var(--accent)}.resource-preview img{display:block;width:100%;height:100%;object-fit:contain}.resource-preview span{position:absolute;inset:auto 0 0;padding:13px 6px 4px;background:linear-gradient(transparent,#070b13ed);color:#d3dfef;font-size:10px;line-height:1.4}.with-thumb>div{min-width:0}.resource-title{overflow-wrap:anywhere}
:root{color-scheme:dark;--bg:#080d16;--panel:#101826;--panel2:#151f30;--line:#263448;--text:#e8eef8;--muted:#91a1b7;--accent:#79a6ff;--primary:#4f7ee8;--danger:#e46464;--good:#58c795;--warn:#e5b95f}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.shell{min-height:100vh;display:grid;grid-template-columns:230px minmax(0,1fr)}.sidebar{position:sticky;top:0;height:100vh;padding:24px 16px;background:#0c1320;border-right:1px solid var(--line)}.brand{display:block;margin:0 8px 28px;color:#fff;font-size:20px;font-weight:800;letter-spacing:-.03em}.brand small{display:block;color:var(--muted);font-size:11px;font-weight:500;letter-spacing:.06em;text-transform:uppercase}.nav{display:grid;gap:6px}.nav a{display:flex;justify-content:space-between;padding:10px 12px;border-radius:9px;color:#b9c5d7}.nav a:hover,.nav a.active{background:#1a2940;color:#fff;text-decoration:none}.nav .count{color:#6f839f}
.workspace{min-width:0}.topbar{min-height:76px;display:flex;align-items:center;gap:16px;padding:16px clamp(18px,4vw,46px);border-bottom:1px solid var(--line);background:#0a101bcc;backdrop-filter:blur(12px);position:sticky;top:0;z-index:5}.topbar h1{font-size:22px;margin:0}.topbar p{margin:2px 0 0;color:var(--muted)}.top-actions{margin-left:auto;display:flex;gap:8px}.content{padding:28px clamp(18px,4vw,46px) 60px;max-width:1500px}
.panel,.resource,.stat,.pipeline-step{background:var(--panel);border:1px solid var(--line);border-radius:14px}.panel{padding:20px;margin-bottom:18px}.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}.panel h2,.panel h3{margin:0}.muted{color:var(--muted)}.tiny{font-size:12px}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.empty{padding:42px;text-align:center;color:var(--muted);border:1px dashed #35445a;border-radius:12px}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:18px}.stat{padding:20px}.stat strong{display:block;font-size:34px;line-height:1.1}.stat span{color:var(--muted)}.pipeline{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.pipeline-step{padding:20px;position:relative}.pipeline-step b{display:block;margin-bottom:6px}.pipeline-step .number{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:#25457a;color:#dfeaff;margin-bottom:18px}
.list{display:grid;gap:10px}.resource{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(180px,.8fr) auto;align-items:center;gap:18px;padding:16px}.resource-title{font-weight:700;overflow-wrap:anywhere}.resource-meta{color:var(--muted);font-size:13px;margin-top:4px}.actions{display:flex;justify-content:flex-end;gap:7px;flex-wrap:wrap}.button,button{appearance:none;border:1px solid #38506f;border-radius:8px;background:#1b2a3e;color:#e8eef8;padding:8px 12px;font:inherit;cursor:pointer;text-decoration:none}.button:hover,button:hover{background:#253952;text-decoration:none}.button.primary,button.primary{background:var(--primary);border-color:var(--primary);color:white}.button.danger,button.danger{border-color:#713b42;color:#ffb0b0}.button.ghost,button.ghost{background:transparent}.button:disabled,button:disabled{opacity:.55;cursor:wait}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border-radius:99px;background:#202d40;color:#b8c7db;font-size:12px}.badge.good{background:#153a2d;color:#8be2ba}.badge.warn{background:#44371b;color:#f1cd7a}.badge.bad{background:#431f27;color:#ff9ca8}.badge.running{background:#18395d;color:#9bcaff}.dot{width:7px;height:7px;border-radius:50%;background:currentColor}.progress{height:6px;margin-top:8px;background:#233147;border-radius:99px;overflow:hidden}.progress i{display:block;height:100%;background:#6594ff}.job-error{margin-top:10px;padding:10px;border-radius:8px;background:#2b161b;color:#ffabb4;white-space:pre-wrap;font-size:12px;max-height:130px;overflow:auto}
.thumb{width:76px;height:58px;object-fit:cover;border-radius:8px;border:1px solid var(--line);background:#070b12}.with-thumb{display:flex;align-items:center;gap:12px}.gallery-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}.gallery-card{position:relative;display:block;aspect-ratio:1.15;padding:5px;border:1px solid var(--line);border-radius:10px;background:#0a111d;overflow:hidden}.gallery-card img{width:100%;height:100%;object-fit:cover;border-radius:6px}.gallery-card span{position:absolute;inset:auto 5px 5px;padding:22px 8px 7px;background:linear-gradient(transparent,#05080ee8 45%);color:#fff;font-size:11px;border-radius:0 0 6px 6px}
.flash{margin-bottom:18px;padding:12px 14px;border-radius:10px;border:1px solid}.flash.ok{background:#123126;border-color:#295a45;color:#a3e5c6}.flash.error{background:#321820;border-color:#67303c;color:#ffb5be}
dialog{width:min(620px,calc(100vw - 28px));padding:0;border:1px solid #34445c;border-radius:15px;background:#101826;color:var(--text);box-shadow:0 30px 90px #000a}dialog::backdrop{background:#02050bc9;backdrop-filter:blur(3px)}.modal-head{display:flex;align-items:center;justify-content:space-between;padding:17px 20px;border-bottom:1px solid var(--line)}.modal-head h2{margin:0;font-size:18px}.modal-body{padding:20px}.modal-actions{display:flex;justify-content:flex-end;gap:8px;padding:15px 20px;border-top:1px solid var(--line)}.icon-button{border:0;background:transparent;font-size:20px;padding:3px 8px}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field{display:grid;gap:6px}.field.full{grid-column:1/-1}.field label{font-size:13px;color:#bcc8d8}.field input,.field select{width:100%;padding:10px 11px;border:1px solid #34445b;border-radius:8px;background:#0a111d;color:var(--text);font:inherit}.check{display:flex;gap:9px;align-items:flex-start;color:#c8d2df}.check input{margin-top:4px}.danger-note{padding:11px;border-radius:8px;background:#2d171c;color:#f3aeb6}
.media-view{display:grid;place-items:center;min-height:420px;background:#050911;border:1px solid var(--line);border-radius:12px;overflow:hidden}.media-view img,.media-view video{display:block;max-width:100%;max-height:74vh}.media-view video{width:100%;background:#000}
.run-artifacts{display:flex;gap:7px;flex-wrap:wrap}.task-timing,.views-timing-slot{line-height:1.65}
.suggestion-panel{padding:12px;border:1px solid #334f70;border-radius:10px;background:#132236;margin:14px 0}.suggestion-panel .actions{margin-top:10px;justify-content:flex-start}.suggestion-hint{color:#9bb8dd;font-size:12px;line-height:1.55}.suggestion-hint button{display:block;margin-top:4px;padding:3px 7px;font-size:11px}.suggestion-warning{color:#edc780;font-size:12px;margin-top:8px}.view-estimate{padding:11px;margin-top:14px;background:#0b1524;border-radius:8px;font-size:13px;line-height:1.65}
dialog[open]{display:grid;grid-template-rows:auto minmax(0,1fr) auto;max-height:calc(100dvh - 32px)}.modal-body{overflow-y:auto;min-height:0}
dialog:has([data-capture-preview]){width:min(1000px,calc(100vw - 28px))}.capture-preview{margin:14px 0;padding:14px;background:#0a1422;border:1px solid #30445e;border-radius:12px}.capture-heading{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap;margin:10px 0}.capture-preview video{width:100%;max-height:190px;background:#03070d;border-radius:8px}.clip-range{display:grid;grid-template-columns:150px 1fr;gap:12px;align-items:center;margin:12px 0}.clip-range label{display:grid;gap:4px;font-size:12px}.clip-range input[type=number]{width:100%;padding:7px;background:#101d30;color:var(--text);border:1px solid #3a4c68;border-radius:6px}.clip-range small{color:var(--muted)}.capture-slider{display:flex;align-items:center;gap:14px;margin:14px 0;font-size:13px}.capture-slider input{flex:1;min-width:0}input[type=range]{accent-color:#7faeff;cursor:pointer}.capture-images{display:grid;grid-template-columns:1fr 1fr;gap:12px}.capture-images figure{margin:0;background:#050911;border-radius:9px;overflow:hidden;border:1px solid #263750}.capture-images img{display:block;width:100%;height:220px;object-fit:contain}.capture-images figcaption{padding:8px;font-size:12px;color:#afbdd2}.capture-images .loading{opacity:.55}.capture-filmstrip{display:flex;gap:7px;overflow-x:auto;padding-bottom:6px}.capture-filmstrip button{flex:0 0 103px;padding:4px;background:#0e1b2c;border:1px solid #2f425e}.capture-filmstrip button.active{border-color:#8db8ff;background:#233d61}.capture-filmstrip img{width:100%;height:68px;object-fit:contain;display:block}.capture-filmstrip span{display:block;font-size:10px;margin-top:4px}.capture-auto{margin-top:14px}.capture-auto small{display:block;color:var(--muted)}.capture-advanced{border:1px solid #2c3c55;border-radius:9px;margin-top:12px;padding:12px}.capture-advanced summary{cursor:pointer;margin-bottom:10px;color:#b3cbed}.capture-advanced .fields{margin-top:12px}[hidden]{display:none!important}
@media(max-width:600px){.capture-images{grid-template-columns:1fr}.capture-images img{height:180px}.clip-range{grid-template-columns:120px 1fr}.capture-slider{flex-wrap:wrap}.capture-slider input{flex-basis:100%}}
@media(max-width:850px){.shell{grid-template-columns:1fr}.sidebar{position:static;height:auto;padding:14px 16px;border-right:0;border-bottom:1px solid var(--line)}.brand{margin:0 0 10px}.brand small{display:inline;margin-left:7px}.nav{grid-template-columns:repeat(4,1fr)}.nav a{justify-content:center}.nav .count{display:none}.topbar{position:static}.resource{grid-template-columns:1fr}.actions{justify-content:flex-start}.stats,.pipeline{grid-template-columns:1fr}.fields{grid-template-columns:1fr}}
"""


APP_SCRIPT = """
function updateResourcePreview(card, status) {
  const view = card.querySelector('[data-preview-kind="views"] img');
  if (view && status.views?.state === 'ready' && status.views.run_id && view.dataset.viewRun !== status.views.run_id) {
    const url = new URL(view.src); url.searchParams.set('v', status.views.run_id);
    view.src = url.href; view.hidden = false; view.dataset.viewRun = status.views.run_id;
  }
  const preview = card.querySelector('[data-run-preview]');
  if (preview && status.state === 'done' && status.model) {
    const target = '/model/' + encodeURIComponent(status.model);
    if (preview.getAttribute('href') !== target) {
      preview.href = target; preview.dataset.previewKind = 'model';
      const image = preview.querySelector('img');
      image.src = '/api/resource-preview?' + new URLSearchParams({kind:'model', key:status.model}); image.hidden = false;
      image.alt = 'Preview: ' + status.model; preview.querySelector('span').textContent = 'Model';
    }
  }
}
document.querySelectorAll('[data-modal]').forEach(button => button.addEventListener('click', () => {
  const dialog = document.getElementById(button.dataset.modal); if (dialog) dialog.showModal();
}));
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
  if (event.target === dialog) dialog.close();
}));
const dateFormat = new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Bangkok', dateStyle:'short', timeStyle:'medium'});
function formatDuration(value) {
  let seconds = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(seconds/3600); seconds %= 3600;
  const minutes = Math.floor(seconds/60); seconds %= 60;
  return [hours ? hours+' ชม.' : '', minutes ? minutes+' นาที' : '', seconds+' วินาที'].filter(Boolean).join(' ');
}
function renderTiming(slot, status, label) {
  if (!slot) return;
  const parts = [];
  if (status.started_at) {
    parts.push(label+' · เริ่ม '+dateFormat.format(new Date(status.started_at))+' (UTC+7)');
    if (status.finished_at) parts.push('เสร็จ '+dateFormat.format(new Date(status.finished_at)));
    let elapsed = Number(status.elapsed_seconds || 0);
    if (status.state === 'running' && !status.finished_at) elapsed = Math.max(elapsed, (Date.now()-Date.parse(status.started_at))/1000);
    parts.push('ใช้เวลา '+formatDuration(elapsed));
    if (status.state === 'running') {
      if (status.estimate_state === 'available' && Number.isFinite(Number(status.eta_seconds)) && status.estimated_finish_at) {
        parts.push('เหลือประมาณ '+formatDuration(status.eta_seconds));
        parts.push('คาดว่าเสร็จ '+dateFormat.format(new Date(status.estimated_finish_at))+' (UTC+7)');
      } else if (status.estimate_state === 'finishing') parts.push('กำลังสรุปผล / ส่งออก');
      else if (status.estimate_state === 'stale') parts.push('รอความคืบหน้าใหม่เพื่อปรับเวลาคาดการณ์');
      else parts.push('กำลังเก็บข้อมูลเพื่อประเมินเวลาที่เหลือ');
    }
  } else if (status.recorded_at) {
    parts.push(label+' · บันทึกไฟล์ '+dateFormat.format(new Date(status.recorded_at))+' (UTC+7) · ไม่มีระยะเวลาในงานเดิม');
  }
  slot.textContent = parts.join(' · ');
}
function renderStatus(slot, status) {
  if (!slot) return;
  const state = status.state || 'waiting';
  if (state === 'error' && slot.dataset.knownError === (status.error || '')) return;
  slot.dataset.knownError = state === 'error' ? (status.error || '') : '';
  const step = status.step || state;
  const progress = Number(status.progress || 0);
  slot.replaceChildren();
  const badge = document.createElement('span');
  badge.className = 'badge ' + (state === 'done' || state === 'ready' ? 'good' : state === 'error' ? 'bad' : state === 'running' ? 'running' : 'warn');
  const dot = document.createElement('i'); dot.className = 'dot'; badge.append(dot, document.createTextNode(' ' + step)); slot.append(badge);
  if (state === 'running') { const bar=document.createElement('div'); bar.className='progress'; const fill=document.createElement('i'); fill.style.width=progress+'%'; bar.append(fill); slot.append(bar); }
  if (state === 'error' && status.error) {
    const detail=document.createElement('div');detail.className='job-error';
    const title=document.createElement('strong');const first=status.error.split('\\n')[0];
    title.textContent=first.startsWith('Command ') ? 'ขั้นตอนนี้ไม่สำเร็จ เปิดรายละเอียดหรือ Log เพื่อตรวจสาเหตุ' : first;
    const more=document.createElement('details'),summary=document.createElement('summary'),trace=document.createElement('pre');
    summary.textContent='รายละเอียดข้อผิดพลาด';trace.textContent=status.error;trace.style.whiteSpace='pre-wrap';trace.style.overflowWrap='anywhere';
    more.append(summary,trace);detail.append(title,more);slot.append(detail);
  }
  if (status.quality?.compute_device) {
    const device = document.createElement('div'); device.className = 'resource-meta';
    const label = status.quality.compute_device_type === 'Cpu' ? 'CPU' : 'GPU';
    device.textContent = label + ': ' + status.quality.compute_device + ' · ' + status.quality.compute_backend;
    slot.append(device);
  }
  const preparer = status.view_compute || status.quality?.mask_compute;
  if (preparer) {
    const device=document.createElement('div');device.className='resource-meta';
    device.textContent=preparer.backend + ': ' + preparer.device;
    if (preparer.fallback_reasons?.length) device.textContent += ' · ' + preparer.fallback_reasons.join(' · ');
    slot.append(device);
  }
  const timing=document.createElement('div'); timing.className='resource-meta task-timing';
  renderTiming(timing, status, status.stage === 'views' ? 'สร้าง Views' : status.stage === 'poses' ? 'หา Camera Pose' : 'สร้าง Model');
  slot.append(timing);
}
let refreshingJobs = false;
function reloadWhenIdle() {
  if (document.querySelector('form[data-action-busy]')) return false;
  window.location.reload(); return true;
}
async function refreshJobs(force = true) {
  if (refreshingJobs || document.hidden) return;
  refreshingJobs = true;
  try {
  for (const card of document.querySelectorAll('[data-job]')) {
    if (!force && Number(card.dataset.nextPollAt || 0) > Date.now()) continue;
    try {
      const endpoint = card.dataset.modelRun
        ? '/api/run/'+encodeURIComponent(card.dataset.job)+'/'+encodeURIComponent(card.dataset.modelRun)
        : '/api/job/'+encodeURIComponent(card.dataset.job);
      const response=await fetch(endpoint, {cache:'no-store'}); if (!response.ok) continue;
      const status = await response.json(); renderStatus(card.querySelector('.status-slot'), status);
      card.dataset.nextPollAt = Date.now() + (status.busy || ['running','queued'].includes(status.state) ? 2500 : 30000);
      updateResourcePreview(card, status);
      if (card.dataset.jobDetail && status.views?.state==='ready') {
        const count=Number(status.views.frames ?? card.dataset.viewCount);
        if (Number(card.dataset.viewCount)!==count || (status.views.run_id && status.views.run_id!==card.dataset.viewRun)) {
          if (reloadWhenIdle()) return;
        }
      }
      if (card.dataset.jobDetail && status.stage==='model' && status.run_id && status.run_id!==card.dataset.knownModelRun) {
        if (reloadWhenIdle()) return;
      }
      if (card.dataset.sourceHub && status.state==='done' && status.model && status.model!==card.dataset.knownModel) {
        if (reloadWhenIdle()) return;
      }
      renderTiming(card.querySelector('.views-timing-slot'), status.views || {}, 'สร้าง Views');
      card.querySelectorAll('[data-create-model]').forEach(button => { button.disabled = Boolean(status.busy); });
      const artifacts=card.querySelector('.run-artifacts');
      if (artifacts && status.state === 'done' && status.model) {
        artifacts.replaceChildren();
        for (const [prefix, title, download] of [['/model/','เปิด Model',false], ['/models/','ดาวน์โหลด PLY',true]]) {
          const link=document.createElement('a'); link.className='button'; link.href=prefix+encodeURIComponent(status.model); link.textContent=title;
          if (download) link.download=''; artifacts.append(link);
        }
        if (status.run_id && status.quality?.eval_views) {const link=document.createElement('a');link.className='button';link.href='/quality/'+encodeURIComponent(card.dataset.job)+'/'+encodeURIComponent(status.run_id);link.textContent='ตรวจภาพ Render';artifacts.append(link);}
      } else if (artifacts && status.state === 'deleted') artifacts.replaceChildren();
    }
    catch (error) { console.warn(error); }
  }
  } finally { refreshingJobs = false; }
}
if (document.querySelector('[data-job]')) {
  refreshJobs(); setInterval(() => refreshJobs(false), 2500);
  document.addEventListener?.('visibilitychange', () => { if (!document.hidden) refreshJobs(); });
}
"""


def modal(dialog_id: str, title: str, body: str, actions: str):
    return (f'<dialog id="{html.escape(dialog_id)}"><div class="modal-head"><h2>{html.escape(title)}</h2>'
            f'<button type="button" class="icon-button" data-close aria-label="ปิด">×</button></div>'
            f'{body}<div class="modal-actions"><button type="button" class="ghost" data-close>ยกเลิก</button>{actions}</div></dialog>')


def upload_source_modal():
    body = '''<form id="upload-source-form" class="modal-body" method="post" action="/actions" enctype="multipart/form-data" data-working>
    <input type="hidden" name="action" value="upload_source"><input type="hidden" name="return_to" value="/sources">
    <div class="field"><label>ภาพหรือวิดีโอ 360</label><input type="file" name="file" accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.mkv,.webm,.insv,.insp" required></div>
    <p class="muted">พร้อมสร้าง 3D: MP4/MOV/JPG แบบ equirectangular 2:1 · ไฟล์ดิบ INSV/INSP อัปโหลดเก็บได้ แต่ต้อง Export เป็น 360 MP4 ด้วย Insta360 Studio ก่อน</p></form>'''
    return modal("upload-source", "เพิ่ม source", body, '<button class="primary" type="submit" form="upload-source-form">อัปโหลด</button>')


def editor_icon(name):
    paths = {
        'dashboard': '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
        'sources': '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
        'jobs': '<path d="m7 4-4 8 4 8m10-16 4 8-4 8M10 12h4"/><circle cx="12" cy="5" r="2"/><circle cx="12" cy="19" r="2"/>',
        'models': '<path d="m12 3 9 5v8l-9 5-9-5V8l9-5Zm0 9 9-4M12 12 3 8m9 4v9"/>',
        'experiments': '<path d="M9 3h6m-5 0v7l-6 9a1.5 1.5 0 0 0 1 2h14a1.5 1.5 0 0 0 1-2l-6-9V3M7 15h10"/>',
        'tutorial': '<path d="M3 5h7l2 2 2-2h7v15h-7l-2 2-2-2H3V5Zm9 2v15"/>',
        'search': '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
        'list': '<path d="M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1"/>',
    }
    return f'<svg class="editor-icon" viewBox="0 0 24 24" aria-hidden="true">{paths.get(name, paths["models"])}</svg>'


def layout(content: str, active="dashboard", title="Dashboard", subtitle="", message="", level="ok", extra_modals="", library=False):
    counts = {"sources": len(source_files()), "jobs": sum(1 for d in JOBS.iterdir() if d.is_dir()),
              "models": len(list(MODELS.glob("*.ply")))}
    nav_items = (("dashboard", "/", "Dashboard", ""), ("sources", "/sources", "Sources", counts["sources"]),
                 ("jobs", "/jobs", "Jobs", counts["jobs"]), ("models", "/models", "Models", counts["models"]),
                 ("experiments", "/experiments", "ผลทดสอบวิดีโอจริง", ""),
                 ("benchmarks", "/benchmarks", "เทียบ GPU / Cloud", ""),
                 ("tutorial", "/tutorial", "Tutorial · คู่มือ", ""))
    nav = "".join(f'<a class="{"active" if key == active else ""}" href="{url}"{" aria-current=\"page\"" if key == active else ""}>{editor_icon(key)}<span>{label}</span><span class="count">{count}</span></a>'
                  for key, url, label, count in nav_items)
    flash = f'<div class="flash {"error" if level == "error" else "ok"}">{html.escape(message)}</div>' if message else ""
    library_tools = (f'''<div class="library-toolbar"><label class="library-search" for="library-search">{editor_icon('search')}<input id="library-search" type="search" aria-label="Search workspace items" placeholder="ค้นหาใน {html.escape(title, quote=True)}…" autocomplete="off"></label><span id="library-search-count" class="muted" role="status" aria-live="polite"></span><div class="library-view-controls" role="group" aria-label="รูปแบบรายการ"><button type="button" data-library-view="list" aria-label="แสดงรายการ" aria-pressed="true">{editor_icon('list')}</button><button type="button" data-library-view="grid" aria-label="แสดงการ์ด" aria-pressed="false">{editor_icon('dashboard')}</button></div></div><div id="library-no-results" class="empty" hidden>ไม่พบรายการที่ตรงกับคำค้น</div>'''
                     if library else '')
    return f'''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(title)} · 360 Studio</title><style>{APP_STYLE}
    .lineage{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:15px 18px;border:1px solid #2b3b52;border-radius:12px;background:#101a2b;margin-bottom:18px;font-size:13px;overflow-wrap:anywhere}}.lineage small{{flex-basis:100%;color:#9cacbf}}.lineage .separator{{color:#728198}}.section-tabs{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}}.view-group{{padding:12px 18px;background:#111e32;border-block:1px solid #2b3b52;display:flex;flex-wrap:wrap;gap:12px;align-items:center}}.view-group small{{color:#a5b7cd}}.filter-summary{{padding:13px 18px;margin-bottom:18px;background:#17263d;border-radius:10px;display:flex;flex-wrap:wrap;gap:12px}}section[id]{{scroll-margin-top:20px}}
    </style><link rel="stylesheet" href="/assets/editor.css"></head><body class="library-editor"><div class="shell">
    <aside class="sidebar"><a class="editor-brand" href="/"><span class="brand-mark">{editor_icon('models')}</span><span>360 Studio<small>GAUSSIAN WORKSPACE</small></span></a><div class="project-switch"><span class="eyebrow">PROJECT</span><strong>360 → 3D</strong><span class="muted">Local workspace</span></div><div class="workspace-label">WORKSPACE</div><nav class="nav" aria-label="เมนูหลัก">{nav}</nav><div class="sidebar-footer"><span class="status-dot"></span><span>Local workspace<br><small>Source · Views · Models</small></span></div></aside>
    <main class="workspace"><header class="topbar"><div><span class="eyebrow">WORKSPACE / {html.escape(active)}</span><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div><div class="top-actions"><a class="button" href="/models">{editor_icon('models')} Models</a><button class="primary" type="button" data-modal="upload-source">+ เพิ่ม Source</button></div></header>
    <div class="content">{flash}{library_tools}{content}</div></main></div>{upload_source_modal()}{extra_modals}<script>{APP_SCRIPT}</script><script type="module" src="/assets/view_settings.mjs"></script><script type="module" src="/assets/capture_preview.mjs"></script><script type="module" src="/assets/editor_ui.mjs"></script><script type="module" src="/assets/action_feedback.mjs"></script></body></html>'''


def settings_fields(settings, suggestions=False, auto_model=False):
    projection = settings.get("projection", "auto")
    option = lambda value, label: f'<option value="{value}"{" selected" if projection == value else ""}>{label}</option>'
    quality = settings.get("quality", "high")
    quality_option = lambda value, label: f'<option value="{value}"{" selected" if quality == value else ""}>{label}</option>'
    hint = lambda key: (f'<div class="suggestion-hint"><span data-suggestion-for="{key}"></span><button type="button" data-use-setting="{key}" disabled>ใช้ค่าแนะนำ</button></div>' if suggestions else "")
    panel = ('''<section class="suggestion-panel" aria-label="คำแนะนำการสร้าง Views"><b>ค่าที่เหมาะกับไฟล์นี้</b><div class="resource-meta" data-source-profile></div><div class="resource-meta" data-suggestion-note aria-live="polite">เปิดฟอร์มเพื่อคำนวณคำแนะนำจากไฟล์และประวัติ</div><div data-suggestion-warnings></div><div class="field"><label>Template จาก Models ที่สำเร็จ</label><select data-template-select disabled><option value="">กำลังอ่าน templates…</option></select></div><div class="resource-meta" data-template-info></div><div class="actions"><button type="button" data-apply-template disabled>ใช้ Template</button><button type="button" data-apply-suggestions disabled>ใช้ค่าที่แนะนำทั้งหมด</button></div></section>''' if suggestions else "")
    preview = ((ROOT / "capture_preview.html").read_text(encoding="utf-8")
               .replace("__START__", str(settings.get("start_seconds", 0)))
               .replace("__END__", str(settings.get("end_seconds", 0)))
               .replace("__AUTO__", "checked" if auto_model else "")
               .replace("__SAMPLING__", f'<div class="field"><label>เก็บทุกกี่เฟรม (ปรับแล้วดูภาพด้านล่างได้เลย)</label><input type="number" name="sample_every" min="1" max="10000" value="{int(settings["sample_every"])}" required>{hint("sample_every")}</div>')
               if suggestions else "")
    advanced_start = '<details class="capture-advanced"><summary>ปรับขนาดภาพและมุมเอง · ใช้ค่าแนะนำได้เลย</summary>' if suggestions else ''
    advanced_end = '</details>' if suggestions else ''
    sample_field = '' if suggestions else f'<div class="field"><label>เก็บทุกกี่เฟรม</label><input type="number" name="sample_every" min="1" max="10000" value="{int(settings["sample_every"])}" required></div>'
    return f'''<div class="field"><label>คุณภาพ Gaussian Splat</label><select name="quality">{quality_option('preview','รวดเร็ว · ทดลองพื้นที่เล็ก')}{quality_option('high','สมดุล · เก็บรอบตัว พื้นและเพดาน (แนะนำ)')}{quality_option('ultra','ละเอียด · ใช้เวลานานขึ้น')}</select>{hint('quality')}</div>''' + preview + panel + advanced_start + f'''<div class="fields">
    <div class="field"><label>ความกว้างภาพ</label><input type="number" name="width" min="160" max="2048" value="{int(settings['width'])}" required>{hint('width')}</div>
    <div class="field"><label>ความสูงภาพ</label><input type="number" name="height" min="160" max="2048" value="{int(settings['height'])}" required>{hint('height')}</div>
    <div class="field"><label>Field of view</label><input type="number" name="fov" min="30" max="150" step="any" value="{float(settings['fov']):g}" required>{hint('fov')}</div>
    <div class="field"><label>มุมแนวนอนต่อเฟรม 360</label><input type="number" name="views" min="1" max="16" value="{int(settings['views'])}" required>{hint('views')}</div>
    {sample_field}
    <div class="field"><label>ชนิดภาพ</label><select name="projection">{option('auto','ตรวจอัตโนมัติ')}{option('panorama','360 equirectangular')}{option('walkthrough','วิดีโอปกติ')}</select>{hint('projection')}</div>
    </div>''' + advanced_end + ('''<div class="view-estimate" data-view-estimate aria-live="polite"></div><div class="suggestion-warning" data-geometry-note></div>''' if suggestions else "")


def live_training_status(job: str, status):
    if not status or status.get("state") != "running" or status.get("stage") != "model":
        return status
    directory = JOBS / job
    log_file = (directory / "model_runs" / status["run_id"] / "3d.log"
                if status.get("run_id") else directory / "3d.log")
    try:
        with log_file.open("rb") as handle:
            handle.seek(max(0, log_file.stat().st_size - 32768))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return status
    if 'masking' in status.get('step','').lower():
        dataset = directory / 'brush_data'
        total = len(read_json(dataset / 'transforms.json',{}).get('frames',[]))
        count = sum(1 for path in (dataset / 'masks').glob('*.png'))
        if total:
            return {**status,'mask_images_written':count,'mask_images_total':total,
                    'step':f'เตรียม Mask คนและผู้ถือกล้อง · {count}/{total} ภาพ'}
    if int(status.get('progress',0)) < 29 and 'match' in status.get('step','').lower():
        pairs = re.findall(r'Processing image \[(\d+)/(\d+)\]',tail)
        if pairs:
            current, count = map(int,pairs[-1])
            return {**status,'progress':21+int(7*current/max(1,count)),
                    'matching_image':current,'matching_images':count,
                    'step':f'COLMAP: จับคู่ภาพบน {"CUDA" if "CUDA" in status.get("step", "") else "CPU"} · {current}/{count} panoramas'}
    if int(status.get('progress',0)) == 29 and 'pose' in status.get('step','').lower():
        registered = re.findall(r'num_reg_frames=(\d+)',tail)
        if registered:
            count = int(registered[-1])
            return {**status,'registered_positions_so_far':count,
                    'step':f'COLMAP: กำลังหา Camera Pose · ลงทะเบียนแล้ว {count} ตำแหน่ง'}
    if 'Done training!' in tail or re.search(r'Eval iter \d+:', tail):
        return {**status, 'progress':95, 'phase':'finalize',
                'step':'กำลังประเมินผล / ส่งออกโมเดล'}
    matches = re.findall(r"Refine iter (\d+), (\d+) splats", tail)
    total = int(status.get("training_steps", 0))
    if not matches or not total:
        return status
    iteration, splats = map(int, matches[-1])
    return {**status, "progress": min(99, 40 + int(min(1, iteration / total) * 55)),
            "iteration": iteration, "splats": splats,
            "step": f'Training {status.get("training_views", "")} views · step {iteration:,}/{total:,} · {splats:,} splats'}


@lru_cache(maxsize=64)
def _estimate_log_tail(filename, size, modified_ns):
    with Path(filename).open('rb') as handle:
        handle.seek(max(0, size - 32768))
        return handle.read().decode('utf-8', errors='replace')


def estimate_log_tail(path):
    try:
        stat = path.stat()
        return _estimate_log_tail(str(path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return ''


def task_estimated_status(job, status):
    if not status or status.get('state') != 'running':
        return status
    if status.get('stage') == 'views':
        return estimate_status(status)
    log_path = JOBS / job / 'model_runs' / str(status.get('run_id', '')) / '3d.log'
    records = compatible_history(status, [read_json(path, {}) for path in MODELS.glob('*.meta.json')])
    history = []
    for record in sorted(records, key=lambda item:item.get('finished_at') or '', reverse=True)[:20]:
        record = dict(record)
        if not record.get('phase_durations_seconds', {}).get('finalize'):
            samples = training_samples(estimate_log_tail(JOBS / str(record.get('job', '')) / 'model_runs' / str(record.get('run_id', '')) / '3d.log'))
            finish = estimate_timestamp(record.get('finished_at'))
            if finish and len(samples) >= 2:
                first, last = samples[0], samples[-1]
                rate = (last[0] - first[0]).total_seconds() / (last[1] - first[1])
                missing = max(0, record.get('quality', {}).get('training_steps', last[1]) - last[1])
                record['finalization_seconds'] = max(0, (finish - last[0]).total_seconds() - missing * rate)
        history.append(record)
    return estimate_status(status, log=estimate_log_tail(log_path), history=history)


def combined_job_status(job: str):
    return task_estimated_status(job, _combined_job_status(job))


def _combined_job_status(job: str):
    view = views_status(job)
    if view.get("state") in {"running", "error", "waiting"}:
        return view
    pose = read_json(JOBS / job / "pose_status.json", {})
    if pose.get("state") == "running":
        return pose
    model = model_status(job)
    if pose.get("state") == "error" and (model or {}).get("state") != "running":
        return pose
    if model and model.get("state") == "done" and not model_file_for_job(job):
        model = None
    return live_training_status(job, model) or pose or view


def timing_markup(status, label=""):
    parts = []
    if status.get("started_at"):
        parts.append(f'{label} · เริ่ม {format_timestamp(status["started_at"])} (UTC+7)')
        if status.get("finished_at"):
            parts.append(f'เสร็จ {format_timestamp(status["finished_at"])}')
        parts.append(f'ใช้เวลา {format_duration(status.get("elapsed_seconds", 0))}')
        if status.get('state') == 'running':
            if status.get('estimate_state') == 'available' and status.get('estimated_finish_at'):
                parts.append(f'เหลือประมาณ {format_duration(status["eta_seconds"])}')
                parts.append(f'คาดว่าเสร็จ {format_timestamp(status["estimated_finish_at"])} (UTC+7)')
            elif status.get('estimate_state') == 'finishing':
                parts.append('กำลังสรุปผล / ส่งออก')
            elif status.get('estimate_state') == 'stale':
                parts.append('รอความคืบหน้าใหม่เพื่อปรับเวลาคาดการณ์')
            else:
                parts.append('กำลังเก็บข้อมูลเพื่อประเมินเวลาที่เหลือ')
    elif status.get("recorded_at"):
        parts.append(f'{label} · บันทึกไฟล์ {format_timestamp(status["recorded_at"])} (UTC+7) · ไม่มีระยะเวลาในงานเดิม')
    return html.escape(" · ".join(parts))


def status_markup(job: str = "", status=None):
    status = combined_job_status(job) if status is None else status
    if status.get('state') == 'running':
        status = task_estimated_status(job or status.get('job', ''), status)
    state = status.get("state", "waiting")
    css = "good" if state in {"done", "ready"} else "bad" if state == "error" else "running" if state == "running" else "warn"
    step = html.escape(str(status.get("step", state)))
    progress = int(status.get("progress", 0))
    detail = str(status.get('error',''))
    first_line = detail.splitlines()[0] if detail else 'ขั้นตอนนี้ไม่สำเร็จ'
    if first_line.startswith('Command '):
        first_line = 'ขั้นตอนนี้ไม่สำเร็จ เปิดรายละเอียดหรือ Log เพื่อตรวจสาเหตุ'
    error = f'<div class="job-error"><strong>{html.escape(first_line)}</strong><details><summary>รายละเอียดข้อผิดพลาด</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">{html.escape(detail)}</pre></details></div>' if state == 'error' else ''
    bar = f'<div class="progress"><i style="width:{progress}%"></i></div>' if state == "running" else ""
    quality = status.get("quality", {})
    device = ""
    if quality.get("compute_device"):
        label = "CPU" if quality.get("compute_device_type") == "Cpu" else "GPU"
        device = (f'<div class="resource-meta">{label}: '
                  f'{html.escape(str(quality["compute_device"]))} · '
                  f'{html.escape(str(quality.get("compute_backend", "")))}</div>')
    label = {"views": "สร้าง Views", "poses": "หา Camera Pose"}.get(status.get("stage"), "สร้าง Model")
    timing = f'<div class="resource-meta task-timing">{timing_markup(status, label)}</div>'
    return f'<div class="status-slot"><span class="badge {css}"><i class="dot"></i>{step}</span>{bar}{error}{device}{timing}</div>'


def model_history_markup(job: str):
    rows = []
    current_view = views_status(job).get("run_id")
    groups = {}
    for status in model_runs(job):
        groups.setdefault(status.get("view_run_id"), []).append(status)
    ordered = sorted(groups, key=lambda key: (key == current_view, key or ""), reverse=True)
    for view_run in ordered:
        same = groups[view_run]
        view = read_json(JOBS / job / "view_runs" / str(view_run) / "status.json", {}) if view_run else {}
        label = 'ชุด Views ปัจจุบัน' if view_run == current_view and view_run else 'ชุด Views ก่อนหน้า' if view_run else 'งานเดิม · ไม่มีรหัสชุด Views'
        rows.append(f'<div class="view-group"><strong>{label}</strong><small>{html.escape(view_run or "legacy")} · {timing_markup(view, "Views")}</small><a href="{models_url(job, view_run)}">ดู Models ของชุดนี้ ({sum(1 for run in same if run.get("model") and (MODELS / Path(run["model"]).name).is_file())}) →</a></div>')
        for status in same:
            rows.append(model_history_row(job, status))
    return f'''<section class="panel" id="models"><div class="panel-head"><div><h2>Models ทั้งหมดของงานนี้</h2><div class="muted">แยกตามชุด Views ที่ใช้จริง · ภาพ Gallery ด้านล่างเป็นชุดปัจจุบัน</div></div><a href="{models_url(job)}">Model library →</a></div><div class="list">{"".join(rows) if rows else '<div class="empty">ยังไม่มีรอบสร้าง model</div>'}</div></section>'''


def model_history_row(job, status):
    identifier = status.get("run_id", "")
    filename = status.get("model", "")
    name = filename or ("รอบ " + identifier)
    attributes = (f' data-job="{html.escape(job)}" data-model-run="{html.escape(identifier)}"'
                  if identifier else "")
    links = ""
    if filename and status.get("state") == "done" and (MODELS / Path(filename).name).is_file():
        url = quote(filename, safe="")
        links = f'<a class="button" href="/model/{url}">เปิด Model</a><a class="button" href="/models/{url}" download>ดาวน์โหลด PLY</a>'
    log_link = (f'<a class="button" href="/jobs/{quote(job, safe="")}/model_runs/{quote(identifier, safe="")}/3d.log" target="_blank">Log</a>'
                if identifier and (JOBS / job / "model_runs" / identifier / "3d.log").is_file() else "")
    if identifier and status.get("state") == "done" and quality_available(job, identifier):
        log_link += f'<a class="button" href="/quality/{quote(job,safe="")}/{quote(identifier,safe="")}">ตรวจภาพ Render</a>'
    settings = status.get("settings", {})
    preset = settings.get("quality") or status.get("quality", {}).get("quality_preset", "")
    selection = status.get("selection", {})
    if selection:
        preset += f' · เลือก {selection["positions"]}/{selection["total_positions"]} ตำแหน่ง ({selection["views"]} ภาพ)'
    return f'''<article class="resource"{attributes}><div class="with-thumb">{resource_preview_markup('model', Path(filename).name, '/model/' + quote(Path(filename).name, safe=''), run=True) if filename and (MODELS / Path(filename).name).is_file() else resource_preview_markup('views', job, '/job/' + quote(job, safe=''), run=True)}<div><div class="resource-title mono">{html.escape(name)}</div><div class="resource-meta">{html.escape(preset)} · {html.escape(identifier or 'งานเดิม')}</div></div></div><div>{status_markup(status=status)}</div><div class="actions"><span class="run-artifacts">{links}</span>{log_link}</div></article>'''


def dashboard_page(message="", level="ok"):
    sources = source_files()
    jobs = sorted((d for d in JOBS.iterdir() if d.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    models = sorted(MODELS.glob("*.ply"), key=lambda p: p.stat().st_mtime, reverse=True)
    overview = ''
    if models:
        model = models[0]
        metadata = model_info(model)
        image_url = resource_preview_url('model', model.name)
        assets, mode, preview_label = resource_preview_assets('model', model.name, metadata)
        if assets and mode == 'image' and assets[0].is_relative_to(JOBS.resolve()):
            image_url = '/jobs/' + quote(assets[0].relative_to(JOBS.resolve()).as_posix(), safe='/')
        model_url = '/model/' + quote(model.name, safe='')
        quality = metadata.get('quality', {})
        fast = available_variant(model)
        profile = metadata.get('source_profile', {})
        resolution = f'{profile.get("width", "—")} × {profile.get("height", "—")}'
        properties = [('Format', 'Gaussian Splat · PLY'),
                      ('Source resolution', resolution),
                      ('Gaussians', f'{int(quality.get("published_splats") or 0):,}'),
                      ('Full model', format_bytes(model.stat().st_size)),
                      ('Fast model', format_bytes(fast['bytes']) if fast else 'ยังไม่มีรุ่นบีบอัด')]
        property_html = ''.join(f'<div><dt>{html.escape(key)}</dt><dd>{html.escape(value)}</dd></div>' for key, value in properties)
        overview = f'''<section class="overview-grid" aria-label="โมเดลล่าสุด"><a class="overview-scene" href="{model_url}"><img src="{html.escape(image_url, quote=True)}" alt="Preview โมเดลล่าสุด" fetchpriority="high"><span class="scene-top">{editor_icon('models')} {html.escape(preview_label)}</span><div class="scene-bottom"><div class="eyebrow">LATEST MODEL</div><h2>{html.escape(model.name)}</h2><p>Walk · Free view · Xbox · Mini-map</p><span class="scene-open">เปิด Model editor ↗</span></div></a><aside class="overview-inspector"><span class="eyebrow">ASSET INSPECTOR</span><h2>โมเดลล่าสุด</h2><dl class="inspector-properties">{property_html}</dl><div class="actions"><a class="button" href="/models">ดู Models ทั้งหมด →</a></div></aside></section>'''
    recent = []
    for directory in jobs[:5]:
        info = job_manifest(directory.name)
        preview = resource_preview_markup('views', directory.name, '/job/' + quote(directory.name, safe=''))
        recent.append(f'<div class="resource" data-job="{html.escape(directory.name)}"><div class="with-thumb">{preview}<div><div class="resource-title"><a href="/job/{html.escape(directory.name)}">{html.escape(info.get("label", directory.name))}</a></div><div class="resource-meta mono">{html.escape(directory.name)} · {info.get("frames",0)} views</div></div></div><div>{status_markup(directory.name)}</div><div class="actions"><a class="button" href="/jobs">จัดการ</a></div></div>')
    content = f'''{overview}<section class="stats"><div class="stat"><strong>{len(sources)}</strong><span>Source files</span></div><div class="stat"><strong>{len(jobs)}</strong><span>Jobs</span></div><div class="stat"><strong>{len(models)}</strong><span>3D models</span></div></section>
    <section class="panel"><div class="panel-head"><div><h2>ขั้นตอนทำงาน</h2><div class="muted">จัดการแต่ละขั้นแยกกันและย้อนกลับมาแก้ค่าได้</div></div></div><div class="pipeline">
    <a class="pipeline-step" href="/sources"><span class="number">1</span><b>Sources</b><span class="muted">อัปโหลดและตรวจภาพหรือวิดีโอ 360</span></a>
    <a class="pipeline-step" href="/jobs"><span class="number">2</span><b>Jobs</b><span class="muted">แตก perspective views และหา camera poses</span></a>
    <a class="pipeline-step" href="/models"><span class="number">3</span><b>Models</b><span class="muted">เปิด ดู ดาวน์โหลด และจัดการ Gaussian Splat</span></a></div></section>
    <section class="panel"><div class="panel-head"><h2>งานล่าสุด</h2><a href="/jobs">ดูทั้งหมด →</a></div><div class="list">{"".join(recent) if recent else '<div class="empty">ยังไม่มี job</div>'}</div></section>'''
    return layout(content, "dashboard", "Dashboard", "ภาพรวม Source → Views → Gaussian Splat", message, level)


def sources_page(message="", level="ok"):
    rows, dialogs = [], []
    source_jobs, source_models = {}, {}
    for directory in JOBS.iterdir():
        if directory.is_dir():
            key = job_manifest(directory.name).get('source_key', '')
            source_jobs.setdefault(key, []).append(directory.name)
    for model in MODELS.glob('*.ply'):
        key = lineage_source(model_info(model))[0]
        source_models[key] = source_models.get(key, 0) + 1
    for index, (key, label, source) in enumerate(source_files()):
        kind = "raw" if label == "data/raw" else "uploads"
        linked = source_jobs.get(key, [])
        key_e, name_e = html.escape(key, quote=True), html.escape(source.name)
        gen_id, rename_id, delete_id = f"source-gen-{index}", f"source-rename-{index}", f"source-delete-{index}"
        is_raw_insta360 = source.suffix.lower() in RAW_INSTA360
        create_action = (
            f'<a class="button primary" href="{source_route(kind, source, preview=True)}">วิธี Export</a>'
            if is_raw_insta360 else
            f'<button class="primary" type="button" data-modal="{gen_id}">สร้าง 3D / Preview Views</button>'
        )
        model_count = source_models.get(key, 0)
        rows.append(f'''<article class="resource"><div class="with-thumb">{resource_preview_markup('source', key, source_route(kind, source, preview=True))}<div><div class="resource-title"><a href="{source_route(kind, source, preview=True)}">{name_e}</a></div>{source_resolution_markup(source)}<div class="resource-meta">{html.escape(label)} · {format_bytes(source.stat().st_size)} · <a href="/jobs?{html.escape(urlencode({'source':key}),quote=True)}">{len(linked)} ชุด Views</a> · <a href="{models_url(source=key)}">{model_count} Models</a></div></div></div><div><span class="badge"><i class="dot"></i>{html.escape(source.suffix.upper().lstrip('.'))}</span></div><div class="actions"><a class="button" href="{source_route(kind, source, preview=True)}">Source และผลงานทั้งหมด</a>{create_action}<button type="button" data-modal="{rename_id}">เปลี่ยนชื่อ</button><button class="danger" type="button" data-modal="{delete_id}">ลบ</button></div></article>''')
        if not is_raw_insta360:
            gen_body = f'''<form id="{gen_id}-form" class="modal-body" method="post" action="/actions" data-working data-settings-source="{key_e}" data-settings-mode="new"><input type="hidden" name="action" value="generate"><input type="hidden" name="source" value="{key_e}"><input type="hidden" name="return_to" value="/jobs"><div class="field full"><label>ชื่อ job</label><input name="label" value="{html.escape(suggested_job_label(source.stem),quote=True)}" maxlength="100"><small class="muted">ชื่อซ้ำจะเติมลำดับให้ โดยเก็บงานเดิมไว้</small></div>{settings_fields(DEFAULT_VIEW_SETTINGS, suggestions=True, auto_model=True)}</form>'''
            dialogs.append(modal(gen_id, f"สร้าง views · {source.name}", gen_body, f'<button class="primary" type="submit" form="{gen_id}-form">เริ่มประมวลผล</button>'))
        rename_body = f'''<form id="{rename_id}-form" class="modal-body" method="post" action="/actions" data-working><input type="hidden" name="action" value="rename_source"><input type="hidden" name="source" value="{key_e}"><input type="hidden" name="return_to" value="/sources"><div class="field"><label>ชื่อไฟล์ใหม่</label><input name="new_name" value="{name_e}" required></div></form>'''
        dialogs.append(modal(rename_id, "เปลี่ยนชื่อ source", rename_body, f'<button class="primary" type="submit" form="{rename_id}-form">บันทึก</button>'))
        delete_body = f'''<form id="{delete_id}-form" class="modal-body" method="post" action="/actions" data-working><input type="hidden" name="action" value="delete_source"><input type="hidden" name="source" value="{key_e}"><input type="hidden" name="return_to" value="/sources"><p class="danger-note">ลบ {name_e} ออกจากดิสก์</p><label class="check"><input type="checkbox" name="cascade" value="1"><span>ลบ jobs และ models ที่สร้างจาก source นี้ด้วย ({len(linked)} jobs)</span></label></form>'''
        dialogs.append(modal(delete_id, "ยืนยันการลบ source", delete_body, f'<button class="danger" type="submit" form="{delete_id}-form">ลบ source</button>'))
    content = f'''<section class="panel"><div class="panel-head"><div><h2>Source library</h2><div class="muted">ไฟล์จาก data/raw และไฟล์ที่อัปโหลดผ่านเว็บ</div></div><button class="primary" type="button" data-modal="upload-source">+ อัปโหลด</button></div><div class="list">{"".join(rows) if rows else '<div class="empty">ยังไม่มี source</div>'}</div></section>'''
    return layout(content, "sources", "Sources", "นำเข้า ตรวจ และสร้าง perspective views", message, level, "".join(dialogs), library=True)


def jobs_page(message="", level="ok", source_filter=""):
    rows, dialogs = [], []
    directories = sorted((d for d in JOBS.iterdir() if d.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    for index, directory in enumerate(directories):
        job = directory.name
        info = job_manifest(job)
        if source_filter and info.get("source_key") != source_filter:
            continue
        settings = info.get("settings", DEFAULT_VIEW_SETTINGS)
        first = next(iter(sorted(directory.glob("frame_*.jpg"))), None)
        thumb = resource_preview_markup('views', job, '/job/' + quote(job, safe=''))
        model = model_file_for_job(job)
        edit_id, train_id, delete_id = f"job-edit-{index}", f"job-train-{index}", f"job-delete-{index}"
        config_id = f'job-config-{index}'
        actions = [f'<a class="button" href="/job/{html.escape(job)}">Gallery</a>',
                   f'<button type="button" data-modal="{config_id}">Check config</button>']
        dialogs.append(config_dialog(job, config_id))
        if (directory / "transforms.json").is_file():
            actions.append(f'<a class="button" href="/poses/{html.escape(job)}">Camera Preview</a>')
        if model:
            actions.append(f'<a class="button primary" href="/model/{html.escape(model.name)}">เปิด Model</a>')
        actions.append(f'<a class="button" href="{models_url(job)}">Models ทั้งหมด ({len(model_files_for_job(job))})</a>')
        if (directory / "transforms.json").is_file():
            disabled = " disabled" if task_is_active(job) else ""
            actions.append(f'<button class="primary" type="button" data-create-model data-modal="{train_id}"{disabled}>สร้าง Model ใหม่</button>')
        actions.extend([f'<button type="button" data-modal="{edit_id}">แก้ไข</button>', f'<button class="danger" type="button" data-modal="{delete_id}">ลบ</button>'])
        view_time = timing_markup(views_status(job), "สร้าง Views")
        rows.append(f'''<article class="resource" data-job="{html.escape(job)}"><div class="with-thumb">{thumb}<div><div class="resource-title"><a href="/job/{quote(job,safe='')}">{html.escape(info.get("label",job))}</a></div><div class="resource-meta">Source: {source_link(info)} · <span class="mono">{html.escape(job)}</span> · {info.get("frames",0)} views</div><div class="resource-meta views-timing-slot">{view_time}</div></div></div><div>{status_markup(job)}</div><div class="actions">{"".join(actions)}</div></article>''')
        edit_body = f'''<form id="{edit_id}-form" class="modal-body" method="post" action="/actions" data-working data-settings-source="{html.escape(info.get('source_key',''),quote=True)}" data-settings-mode="edit"><input type="hidden" name="job" value="{html.escape(job,quote=True)}"><input type="hidden" name="return_to" value="/jobs"><div class="field full"><label>ชื่อ job</label><input name="label" value="{html.escape(info.get('label',job))}" maxlength="100" required></div>{settings_fields(settings, suggestions=True)}</form>'''
        edit_actions = f'<button type="submit" form="{edit_id}-form" name="action" value="update_job">บันทึกชื่อ</button><button class="primary" type="submit" form="{edit_id}-form" name="action" value="reprocess_job">สร้าง Views ใหม่</button>'
        dialogs.append(modal(edit_id, "แก้ไข job", edit_body, edit_actions))
        train_body = model_generation_form(job, train_id, "/job/" + quote(job, safe=""))
        dialogs.append(modal(train_id, "สร้าง Model ใหม่", train_body, f'<button class="primary" type="submit" form="{train_id}-form">เริ่มสร้าง Model ใหม่</button>'))
        delete_body = f'''<form id="{delete_id}-form" class="modal-body" method="post" action="/actions" data-working><input type="hidden" name="action" value="delete_job"><input type="hidden" name="job" value="{html.escape(job,quote=True)}"><input type="hidden" name="return_to" value="/jobs"><p class="danger-note">ลบ job, perspective views, COLMAP data และ log</p><label class="check"><input type="checkbox" name="delete_model" value="1" checked><span>ลบ models ทุกรอบที่เชื่อมกับ job นี้ด้วย</span></label></form>'''
        dialogs.append(modal(delete_id, "ยืนยันการลบ job", delete_body, f'<button class="danger" type="submit" form="{delete_id}-form">ลบ job</button>'))
    content = f'''<section class="panel"><div class="panel-head"><div><h2>Processing jobs</h2><div class="muted">แก้ค่าการแตกภาพ สร้างใหม่ หรือต่อไปเป็น Gaussian Splat</div></div><a class="button primary" href="/sources">+ สร้างจาก Source</a></div><div class="list">{"".join(rows) if rows else '<div class="empty">ยังไม่มี job เลือก source เพื่อเริ่มงาน</div>'}</div></section>'''
    if source_filter:
        content = f'<div class="filter-summary">Views จาก Source: {source_link({"source_key":source_filter})}<a href="/jobs">แสดงทุก Source ×</a></div>' + content
    return layout(content, "jobs", "Jobs", "จัดการ perspective views และการ train", message, level, "".join(dialogs), library=True)


def compression_form(model):
    return (f'<form method="post" action="/actions" style="display:inline-flex">'
            '<input type="hidden" name="action" value="compress_model">'
            f'<input type="hidden" name="model" value="{html.escape(model,quote=True)}">'
            f'<input type="hidden" name="return_to" value="/model/{quote(model,safe="")}">'
            '<button type="submit">บีบอัด Model</button></form>')


def compression_page(identifier, message='', level='ok'):
    record = compression_record(identifier)
    if not record:
        return None
    parent = record.get('parent_model', '')
    parent_exists = Path(parent).name == parent and (MODELS / parent).is_file()
    metadata = {**record.get('parent_info', {}), 'model': parent if parent_exists else ''}
    state = record.get('state', 'error')
    ready = state == 'ready' and (compression_directory(identifier) / 'model.spz').is_file()
    actions = []
    if parent_exists:
        actions.extend([f'<a class="button" href="/model/{quote(parent,safe="")}?variant=full">Model ต้นทาง</a>',
                        f'<a class="button" href="/models/{quote(parent,safe="")}" download>ดาวน์โหลด PLY เต็ม</a>'])
    if ready:
        if parent_exists and record.get('source') == source_signature(MODELS / parent):
            actions.append(f'<a class="button primary" href="/model/{quote(parent,safe="")}?variant=fast&compression={identifier}">เปิด Model บีบอัด</a>')
        actions.append(f'<a class="button" href="/compressed-download/{identifier}.spz" download="{html.escape(Path(parent).stem,quote=True)}.spz">ดาวน์โหลด SPZ บีบอัด</a>')
    details = [('Record', identifier), ('Model ต้นทางที่ใช้บีบอัด', record.get('model', parent)),
               ('สร้างเมื่อ', format_timestamp(record.get('created_at'))), ('เสร็จเมื่อ', format_timestamp(record.get('finished_at'))),
               ('เวลาบีบอัด', format_duration(record['duration_seconds']) if record.get('duration_seconds') is not None else '—'),
               ('ขนาดเต็ม', format_bytes(record.get('source', {}).get('bytes', 0))),
               ('ขนาดบีบอัด', format_bytes(record.get('bytes', 0))),
               ('Gaussians', f'{record.get("splats",0):,}'), ('SH degree', record.get('sh_degree', '—')),
               ('Format / encoder', f'SPZ v{record.get("version",3)} · {record.get("encoder","Spark 2.3.1")}'),
               ('SHA-256 ต้นทาง', record.get('source_sha256', '—')),
               ('SHA-256 บีบอัด', record.get('sha256', '—'))]
    source_bytes = record.get('source', {}).get('bytes', 0)
    if ready and source_bytes:
        details.insert(7, ('ขนาดลดลง', f'{100*(1-record["bytes"]/source_bytes):.1f}%'))
    table = ''.join(f'<tr><td>{html.escape(str(label))}</td><td style="overflow-wrap:anywhere">{html.escape(str(value))}</td></tr>' for label,value in details)
    status_label = {'queued':'รอบีบอัด','running':'กำลังบีบอัด','ready':'พร้อมใช้งาน','error':'บีบอัดไม่สำเร็จ','deleted':'ไฟล์ถูกลบแล้ว'}.get(state,state)
    body = lineage_markup(metadata, 'model') + f'''<section class="panel"><div class="panel-head"><h2>Model บีบอัด · SPZ</h2><span class="badge {'good' if ready else 'warn'}">{html.escape(status_label)}</span></div><div class="actions">{''.join(actions)}<a class="button" href="/models#compressed-models">Models บีบอัดทั้งหมด</a><a class="button" href="/api/compression/{identifier}" target="_blank">Record JSON</a></div><table>{table}</table><p class="muted">เก็บจุดและ SH ทุกระดับเดิม แต่ลดความละเอียดตัวเลขของจุด สี และ SH เพื่อให้ไฟล์เล็กลง การบีบอัดช่วยลดปริมาณดาวน์โหลด ส่วนจำนวนจุดที่ GPU ต้องแสดงยังเท่าเดิม</p></section>'''
    if record.get('error'):
        body += '<section class="panel"><p>' + html.escape(record['error']) + '</p></section>'
    if state in {'queued','running'}:
        body += '<script>setTimeout(()=>location.reload(),3000)</script>'
    if record.get('validation'):
        validation = record['validation']
        body += f'<section class="panel"><h2>ผลตรวจรุ่นบีบอัด</h2><p>{validation["eval_views"]} มุมเดิม · รุ่นเต็ม {validation["full_psnr_db"]:.3f} dB · รุ่นบีบอัด {validation["compressed_psnr_db"]:.3f} dB</p><p class="muted">Render ผ่าน Brush จากค่าจุดที่อ่านกลับด้วย Spark ใช้ masks และภาพอ้างอิงเดิม ไม่ใช่การวัด FPS ของ browser</p></section>'
        if (compression_directory(identifier) / 'comparison.jpg').is_file():
            body += f'<section class="panel"><h2>ภาพเปรียบเทียบ · Source / เต็ม / บีบอัด</h2><a href="/compression-comparison/{identifier}.jpg" target="_blank"><img src="/compression-comparison/{identifier}.jpg" alt="ภาพต้นทาง รุ่นเต็ม และรุ่น SPZ บีบอัด ในมุมกล้องเดียวกัน" style="width:100%;max-width:1152px;height:auto" loading="lazy"></a></section>'
    return layout(body, 'models', 'Model บีบอัด', Path(parent).stem + '.spz', message, level)


def compressed_models_markup(job_filter='', view_filter=None, source_filter=''):
    rows = []
    for record in compression_records():
        metadata = record.get('parent_info', {})
        if not model_matches(metadata, job_filter, view_filter, source_filter):
            continue
        identifier, parent = record['id'], record.get('parent_model', '')
        state = record.get('state')
        if state == 'deleted':
            continue
        record_url = '/compression/' + identifier
        parent_exists = Path(parent).name == parent and (MODELS / parent).is_file()
        actions = [f'<a class="button" href="{record_url}">ดู Record</a>']
        if parent_exists:
            actions.extend([f'<a class="button" href="/model/{quote(parent,safe="")}?variant=full">Model ต้นทาง</a>',
                            f'<a class="button" href="/models/{quote(parent,safe="")}" download>ดาวน์โหลด PLY เต็ม</a>'])
        ready = state == 'ready' and (compression_directory(identifier) / 'model.spz').is_file()
        if ready:
            if parent_exists and record.get('source') == source_signature(MODELS / parent):
                actions.insert(0, f'<a class="button primary" href="/model/{quote(parent,safe="")}?variant=fast&compression={identifier}">เปิดรุ่นบีบอัด</a>')
            actions.append(f'<a class="button" href="/compressed-download/{identifier}.spz" download="{html.escape(Path(parent).stem,quote=True)}.spz">ดาวน์โหลด SPZ บีบอัด</a>')
        size = f'{format_bytes(record.get("source",{}).get("bytes",0))} → {format_bytes(record.get("bytes",0))}' if ready else {'queued':'รอบีบอัด','running':'กำลังบีบอัด','error':'บีบอัดไม่สำเร็จ'}.get(state,state)
        thumb = resource_preview_markup('model', parent, record_url, metadata, deferred=True) if parent_exists else ''
        rows.append(f'''<article class="resource" data-compression-id="{identifier}"><div class="with-thumb">{thumb}<div><div class="resource-title"><a href="{record_url}">{html.escape(Path(record.get('model',parent)).stem)}.spz</a></div><div class="resource-meta">{size} · {record.get('splats',0):,} splats</div><div class="resource-meta">บีบอัด {format_timestamp(record.get('created_at'))} · Source: {source_link(metadata)}</div></div></div><span class="badge {'good' if ready else 'warn'}">SPZ บีบอัด</span><div class="actions">{''.join(actions)}</div></article>''')
    return f'<section class="panel model-library-panel" id="compressed-models" role="tabpanel" aria-labelledby="model-tab-compressed" tabindex="0" hidden><div class="panel-head"><div><h2>Models บีบอัด ({len(rows)})</h2><p class="muted">Source → Views → Model เต็ม → Model บีบอัด · ทุก record เชื่อมกลับไปยังรุ่นต้นทาง</p></div></div><div class="list">' + (''.join(rows) or '<div class="empty">กดบีบอัด Model จากรุ่นเต็มเพื่อสร้าง record</div>') + '</div></section>'


def models_page(message="", level="ok", job_filter="", view_filter=None, source_filter=""):
    rows, dialogs = [], []
    return_url = models_url(job_filter, view_filter, source_filter)
    models = sorted(MODELS.glob("*.ply"), key=lambda p: p.stat().st_mtime, reverse=True)
    for index, model in enumerate(models):
        metadata = model_info(model)
        if not model_matches(metadata, job_filter, view_filter, source_filter):
            continue
        linked_job = metadata.get("job", "")
        link_label = (f'<a href="/job/{quote(linked_job, safe="")}">job {html.escape(linked_job)}</a>'
                      if linked_job and job_directory(linked_job) else
                      "job " + html.escape(linked_job) if linked_job else "uploaded model")
        view_run = metadata.get("view_run_id")
        link_label += f' · Source: {source_link(metadata)} · <a href="{models_url(linked_job, view_run)}">ชุด Views: {html.escape(view_run or "งานเดิม (ไม่มีรหัส)")}</a>' if linked_job else ''
        rename_id, delete_id = f"model-rename-{index}", f"model-delete-{index}"
        rows.append(f'''<article class="resource"><div class="with-thumb">{resource_preview_markup('model', model.name, '/model/' + quote(model.name, safe=''), metadata)}<div><div class="resource-title">{html.escape(model.name)}</div><div class="resource-meta">{format_bytes(model.stat().st_size)} · {link_label}</div><div class="resource-meta">{timing_markup(metadata, "สร้าง Model")}</div></div></div><div><span class="badge good"><i class="dot"></i>Gaussian PLY</span></div><div class="actions"><a class="button primary" href="/model/{html.escape(model.name)}">เปิด Viewer</a><a class="button" href="/models/{html.escape(model.name)}" download>ดาวน์โหลด</a><button type="button" data-modal="{rename_id}">เปลี่ยนชื่อ</button><button class="danger" type="button" data-modal="{delete_id}">ลบ</button></div></article>''')
        fast = available_variant(model)
        download = (f'<a class="button" href="/compressed-download/{fast["record_id"]}.spz" download="{html.escape(model.stem,quote=True)}.spz">ดาวน์โหลด SPZ บีบอัด</a>'
                    f'<a class="button" href="/compression/{fast["record_id"]}">Record บีบอัด</a>') if fast and fast.get('record_id') else '<button disabled>ดาวน์โหลด SPZ บีบอัด · ยังไม่มี</button>'
        rows[-1] = rows[-1].replace('download>ดาวน์โหลด</a>', 'download>ดาวน์โหลด PLY เต็ม</a>', 1).replace('</div></article>', download + compression_form(model.name) + '</div></article>', 1)
        rename_body = f'''<form id="{rename_id}-form" class="modal-body" method="post" action="/actions" data-working><input type="hidden" name="action" value="rename_model"><input type="hidden" name="model" value="{html.escape(model.name,quote=True)}"><input type="hidden" name="return_to" value="{return_url}"><div class="field"><label>ชื่อไฟล์ใหม่</label><input name="new_name" value="{html.escape(model.name)}" required></div></form>'''
        dialogs.append(modal(rename_id, "เปลี่ยนชื่อ model", rename_body, f'<button class="primary" type="submit" form="{rename_id}-form">บันทึก</button>'))
        delete_body = f'''<form id="{delete_id}-form" class="modal-body" method="post" action="/actions" data-working><input type="hidden" name="action" value="delete_model"><input type="hidden" name="model" value="{html.escape(model.name,quote=True)}"><input type="hidden" name="return_to" value="{return_url}"><p class="danger-note">ลบไฟล์ {html.escape(model.name)} ({format_bytes(model.stat().st_size)}) และไฟล์บีบอัดที่เชื่อมกับรุ่นนี้ โดยเก็บ record การบีบอัดไว้</p></form>'''
        dialogs.append(modal(delete_id, "ยืนยันการลบ model", delete_body, f'<button class="danger" type="submit" form="{delete_id}-form">ลบ model</button>'))
    upload_body = '''<form id="upload-model-form" class="modal-body" method="post" action="/actions" enctype="multipart/form-data" data-working><input type="hidden" name="action" value="upload_model"><input type="hidden" name="return_to" value="/models"><div class="field"><label>Gaussian Splat PLY</label><input type="file" name="model" accept=".ply" required></div></form>'''
    dialogs.append(modal("upload-model", "อัปโหลด model", upload_body, '<button class="primary" type="submit" form="upload-model-form">อัปโหลด</button>'))
    content = f'''<section class="panel model-library-panel" id="models-original" role="tabpanel" aria-labelledby="model-tab-original" tabindex="0"><div class="panel-head"><div><h2>Model library</h2><div class="muted">เปิดด้วย Three.js Gaussian Splat viewer หรือดาวน์โหลด PLY</div></div><button class="primary" type="button" data-modal="upload-model">+ อัปโหลด PLY</button></div><div class="list">{"".join(rows) if rows else '<div class="empty">ยังไม่มี model</div>'}</div></section>'''
    content = '''<div class="model-library-tabs"><div role="tablist" aria-label="ประเภท Model"><button id="model-tab-original" role="tab" type="button" data-model-tab="models-original" aria-controls="models-original" aria-selected="true">Models <span>PLY</span></button><button id="model-tab-compressed" role="tab" type="button" data-model-tab="compressed-models" aria-controls="compressed-models" aria-selected="false" tabindex="-1">Models บีบอัด <span>SPZ</span></button></div><div class="model-tab-stepper" role="group" aria-label="เปลี่ยนแท็บ"><button type="button" data-model-tab-step="-1" aria-label="แท็บซ้าย">←</button><button type="button" data-model-tab-step="1" aria-label="แท็บขวา">→</button></div></div><noscript><style>.model-library-panel[hidden]{display:block!important}</style></noscript>''' + content + compressed_models_markup(job_filter, view_filter, source_filter)
    if job_filter or view_filter is not None or source_filter:
        context = job_manifest(job_filter) or {"source_key":source_filter}
        content = (f'<div class="filter-summary">{len(rows)} Models · {source_link(context)}'
                   + (f' · <a href="/job/{quote(job_filter,safe="")}">กลับไป Views ของงาน {html.escape(job_filter)}</a>' if job_directory(job_filter) else '')
                   + (f' · ชุด Views {html.escape(view_filter or "legacy")}' if view_filter is not None else '')
                   + '<a href="/models">แสดงทุก Model ×</a></div>' + content)
    return layout(content, "models", "Models", "ดู ดาวน์โหลด เปลี่ยนชื่อ และลบโมเดล", message, level, "".join(dialogs), library=True)


def model_generation_form(job: str, dialog_id: str, return_to: str):
    return f'''<form id="{html.escape(dialog_id)}-form" class="modal-body" method="post" action="/actions" data-working><input type="hidden" name="action" value="generate3d"><input type="hidden" name="job" value="{html.escape(job,quote=True)}"><input type="hidden" name="return_to" value="{html.escape(return_to,quote=True)}"><p>Brush จะ train Gaussian Splat ด้วย GPU โดยใช้ camera poses ที่ preview แล้ว ถ้ายังไม่มี pose จะหาให้ด้วย COLMAP ก่อน</p><p><a href="/poses/{quote(job,safe='')}">เปิด Camera Preview เพื่อตรวจตำแหน่งและภาพก่อน train</a></p><p class="muted">แต่ละครั้งจะได้ไฟล์ model ใหม่ และเก็บเวลาเริ่ม–สิ้นสุดกับระยะเวลาที่ใช้ไว้ในประวัติ รอรอบปัจจุบันเสร็จแล้วกดสร้างรอบถัดไปได้</p></form>'''


def quality_frames(job: str, identifier: str):
    directory = job_directory(job)
    if directory is None or not re.fullmatch(r"\d{8}_\d{6}_[a-f0-9]{8}", identifier):
        return None
    run_dir = directory / "model_runs" / identifier
    status = read_json(run_dir / "status.json", {})
    quality = status.get("quality", {})
    render_dir = run_dir / "brush_export" / f'eval_{quality.get("training_steps",0)}'
    dataset = run_dir / "dataset" if (run_dir / "dataset").is_dir() else directory / "brush_data"
    if not (run_dir / "dataset").is_dir() and status.get("view_run_id") != views_status(job).get("run_id"):
        return None  # A newer root dataset cannot be compared against this older run.
    parent = job_directory(read_json(directory / "job.json", {}).get("parent_job", ""))
    frames = []
    dataset_frames = read_json(dataset / 'transforms.json', {}).get('frames', [])
    inputs = {Path(frame.get('file_path', '')).stem: dataset / frame.get('file_path', '') for frame in dataset_frames}
    previous_run = status.get('parent_run_id')
    previous_status = read_json(directory / 'model_runs' / str(previous_run) / 'status.json', {}) if previous_run else {}
    previous_renders = (directory / 'model_runs' / str(previous_run) / 'brush_export'
                        / f'eval_{previous_status.get("quality", {}).get("training_steps", 0)}') if previous_run else None
    for image in sorted(render_dir.glob("*.png")):
        original = inputs.get(image.stem, dataset / "images" / (image.stem + ".jpg")).resolve()
        if not original.is_relative_to(dataset.resolve()) or not original.is_file() or not image.resolve().is_relative_to(JOBS.resolve()):
            continue
        mask_path = dataset / "masks" / image.name
        previous = None
        if (run_dir / "baseline" / image.name).is_file():
            previous = run_dir / "baseline" / image.name
        elif previous_renders and (previous_renders / image.name).is_file():
            previous = previous_renders / image.name
        elif parent:
            previous = next((parent / "brush_export").glob("eval_*/" + image.name), None)
        paths = {'source': original, 'render': image.resolve(),
                 'mask': mask_path.resolve() if mask_path.is_file() else None,
                 'previous': previous.resolve() if previous else None}
        if any(path and not path.is_relative_to(JOBS.resolve()) for path in paths.values()):
            continue
        frames.append({'name': image.stem, **paths})
    return status, frames


def model_quality_page(job: str, identifier: str):
    catalog = quality_frames(job, identifier)
    if catalog is None:
        return None
    status, paths = catalog
    quality = status.get('quality', {})
    frames = []
    for item in paths:
        frame = {key: '/jobs/' + quote(value.relative_to(JOBS.resolve()).as_posix(), safe='/') if value else None
                 for key, value in item.items() if key != 'name'}
        frame.update(name=item['name'], metrics_url=f'/api/quality-metrics/{quote(job,safe="")}/{identifier}?{urlencode({"frame":item["name"]})}')
        frames.append(frame)
    if not frames:
        return None
    selection = status.get("selection") or {}
    summary = (f'{selection.get("positions", "ทุก")} ตำแหน่ง · {selection.get("views", quality.get("perspective_views", ""))} ภาพ · '
               f'{quality.get("training_steps",0):,} steps · {format_duration(status.get("elapsed_seconds",0))}<br>'
               f'GPU: {html.escape(quality.get("compute_device", "Brush"))} · '
               f'PSNR เฉลี่ย (บริเวณที่ไม่ถูก mask): {quality.get("eval_psnr_mean_db",0):.2f} dB')
    if selection:
        summary += f'<br>กรองจุดตั้งต้น {selection["original_sparse_points"]:,} → {selection["sparse_points"]:,} จุด'
    if status.get('refined_from'):
        parent_url = '/model/' + quote(status['refined_from'], safe='')
        summary += (f'<br>ปรับละเอียดจาก <a href="{parent_url}">Model รุ่นก่อน</a> · '
                    f'รวม {quality.get("cumulative_training_steps", quality.get("training_steps",0)):,} steps')
        if quality.get('parent_eval_psnr_mean_db') is not None:
            summary += f' · PSNR รุ่นก่อน {quality["parent_eval_psnr_mean_db"]:.2f} dB'
    replacements = {"__JOB_URL__": "/job/"+quote(job,safe=""), "__LINEAGE__": lineage_markup(status, "model"),
        "__MODEL_URL__": "/model/"+quote(status.get("model",""),safe=""),
        "__SUMMARY__": summary, "__CONFIG__": json.dumps(frames,ensure_ascii=False).replace("<","\\u003c")}
    page = (ROOT / "quality_viewer.html").read_text(encoding="utf-8")
    return re.sub(r"__(?:JOB_URL|MODEL_URL|SUMMARY|CONFIG|LINEAGE)__",lambda match:replacements[match.group(0)],page)


def gallery_slice(items, page, url, anchor):
    """Bound gallery HTML and thumbnail requests while keeping every image reachable."""
    total = len(items)
    pages = max(1, (total + 47) // 48)
    try:
        current = max(1, min(pages, int(page)))
    except (ValueError, TypeError):
        current = 1
    start = (current - 1) * 48
    if not total:
        return [], '', 0
    def link(number, label):
        if not 1 <= number <= pages or number == current:
            return f'<span class="button" aria-disabled="true">{label}</span>'
        return f'<a class="button" href="{html.escape(url + "?" + urlencode({"page":number}) + "#" + anchor,quote=True)}">{label}</a>'
    pagination = (f'<nav class="gallery-pagination" aria-label="หน้าภาพ">'
                  f'<span class="muted">ภาพ {start + 1}–{min(start + 48,total)} / {total:,} ภาพ</span>'
                  f'<div>{link(1,"หน้าแรก")}{link(current-1,"← ก่อนหน้า")}'
                  f'<span aria-current="page">หน้า {current} / {pages}</span>'
                  f'{link(current+1,"ถัดไป →")}{link(pages,"หน้าสุดท้าย")}</div></nav>')
    return items[start:start + 48], pagination, start


def model_inputs_page(model, page=1):
    info = model_info(model)
    job = info.get("job", "")
    directory = job_directory(job)
    dataset = directory / "model_runs" / str(info.get("run_id", "")) / "dataset" if directory else None
    if dataset is None or not dataset.is_dir():
        content = '<div class="empty">รุ่นนี้ไม่ได้เก็บสำเนาภาพเทรนไว้ จึงไม่มี Gallery ย้อนหลังของชุดนี้</div>'
    else:
        dataset = dataset.resolve()
        frames = read_json(dataset / "transforms.json", {}).get("frames", [])
        images = []
        for frame in frames:
            image = (dataset / frame.get("file_path", "")).resolve()
            if not image.is_relative_to(dataset) or not image.is_file():
                continue
            images.append((image, frame))
        selected, pagination, _ = gallery_slice(images, page, '/model-inputs/' + quote(model.name,safe=''), 'input-gallery')
        cards = []
        for image, frame in selected:
            url = '/jobs/' + quote(image.relative_to(JOBS).as_posix(), safe='/')
            label = f'เฟรม {frame.get("frame_index", "")} · ทิศ {frame.get("yaw_deg", "")}° / pitch {frame.get("pitch_deg", "")}°'
            thumbnail_url = html.escape(resource_preview_url('view-image', image.relative_to(JOBS).as_posix()), quote=True)
            cards.append(f'<a class="gallery-card" href="{url}" target="_blank" rel="noopener"><img src="{thumbnail_url}" alt="{html.escape(label,quote=True)}" loading="lazy" decoding="async"><span>{html.escape(label)}</span></a>')
        content = f'<section class="panel" id="input-gallery"><div class="panel-head"><h2>ภาพที่ส่งเข้า Trainer ของรุ่นนี้</h2><span class="badge">{len(images)} ภาพ</span></div><p class="muted">เก็บไว้กับ Model รุ่นนี้ รวมภาพที่ Brush เว้นไว้ทดสอบ · คงชุดเดิมแม้สร้าง Views ใหม่</p>{pagination}<div class="gallery-grid">{"".join(cards) if cards else "<div class=empty>ไม่มีภาพในชุดนี้</div>"}</div>{pagination}</section>'
    return layout(lineage_markup(info, "model") + f'<p><a class="button primary" href="/model/{quote(model.name,safe="")}">กลับไป 3D Model</a></p>' + content, "models", "ภาพต้นทางของ Model", model.name)


def job_detail_page(job: str, message="", level="ok", page=1):
    directory = job_directory(job)
    if directory is None:
        return None
    info = job_manifest(job)
    meta = read_json(directory / "transforms.json", {})
    frames = meta.get("frames", [])
    frame_by_name = {Path(frame.get("file_path", "")).name: frame for frame in frames if frame.get("file_path")}
    panorama = info.get("source_mode") == "panorama"
    images = sorted(directory.glob("frame_*.jpg"))
    selected, pagination, start = gallery_slice(images, page, '/job/' + quote(job,safe=''), 'views')
    cards = []
    for ordinal, image in enumerate(selected, start + 1):
        frame = frame_by_name.get(image.name, {})
        if panorama:
            yaw = float(frame.get("yaw_deg", 0))
            pitch = float(frame.get("pitch_deg", 0))
            label = f"ทิศ {yaw:.0f}° · pitch {pitch:+.0f}°"
        else:
            label = f"ภาพที่ {ordinal}"
        if frame.get("frame_index") is not None:
            label += f" · เฟรม {frame['frame_index']}"
        thumbnail_url = html.escape(resource_preview_url('view-image', image.relative_to(JOBS).as_posix()), quote=True)
        image_url = '/jobs/' + quote(job,safe='') + '/' + quote(image.name,safe='')
        cards.append(f'<a class="gallery-card" href="{image_url}" target="_blank" rel="noopener"><img src="{thumbnail_url}" alt="{html.escape(label,quote=True)}" loading="lazy" decoding="async"><span>{html.escape(label)}</span></a>')
    model = model_file_for_job(job)
    model_link = '<div class="run-artifacts">' + (f'<a class="button primary" href="/model/{html.escape(model.name)}">เปิด Model</a>' if model else '') + '</div>'
    model_link += f'<a class="button" href="/poses/{html.escape(job)}">Camera Preview · ดูตำแหน่ง 3D</a>'
    dialog_id = "new-model"
    dialogs = config_dialog(job, 'check-config')
    model_link += '<button type="button" data-modal="check-config">Check config</button>'
    if (directory / "transforms.json").is_file():
        disabled = " disabled" if task_is_active(job) else ""
        model_link += f'<button class="primary" type="button" data-create-model data-modal="{dialog_id}"{disabled}>สร้าง Model ใหม่</button>'
        dialogs += modal(dialog_id, "สร้าง Model ใหม่", model_generation_form(job, dialog_id, "/job/" + quote(job, safe="")),
                        f'<button class="primary" type="submit" form="{dialog_id}-form">เริ่มสร้าง Model ใหม่</button>')
        if source_from_key(info.get('source_key','')) is not None:
            edit_id = 'adjust-job-views'
            edit_body = f'''<form id="{edit_id}-form" class="modal-body" method="post" action="/actions" data-working data-settings-source="{html.escape(info.get('source_key',''),quote=True)}" data-settings-mode="edit"><input type="hidden" name="action" value="reprocess_job"><input type="hidden" name="job" value="{html.escape(job,quote=True)}"><input type="hidden" name="return_to" value="/job/{quote(job,safe='')}"><p class="muted">สร้าง Views ชุดใหม่ในงานเดิม Models ที่สร้างไปแล้วจะอยู่ในประวัติของชุดเดิม</p><div class="field"><label>ชื่องาน</label><input name="label" value="{html.escape(info.get('label',job),quote=True)}" required maxlength="100"></div>{settings_fields(info.get('settings',DEFAULT_VIEW_SETTINGS),suggestions=True)}</form>'''
            dialogs += modal(edit_id,'ปรับช่วงวิดีโอ / สร้าง Views ใหม่',edit_body,f'<button class="primary" type="submit" form="{edit_id}-form">สร้าง Views ใหม่</button>')
            model_link += f'<button type="button" data-create-model data-modal="{edit_id}"{disabled}>ปรับช่วง / สร้าง Views ใหม่</button>'
    view_time = timing_markup(views_status(job), "สร้าง Views")
    content = lineage_markup(info) + f'''<nav class="section-tabs" aria-label="ส่วนของงาน"><a class="button" href="#views">ภาพ Views ({len(images)})</a><a class="button" href="/poses/{quote(job,safe='')}">ตำแหน่งกล้อง 3D</a><a class="button" href="#models">Models ทั้งหมด ({len(model_files_for_job(job))})</a></nav><section class="panel" data-job="{html.escape(job)}" data-job-detail="1" data-view-count="{len(images)}" data-view-run="{html.escape(views_status(job).get("run_id") or "",quote=True)}" data-known-model-run="{html.escape((model_status(job) or {}).get("run_id") or "",quote=True)}"><div class="panel-head"><div><h2>{html.escape(info.get("label",job))}</h2><div class="muted">Source: {source_link(info)} · <span class="mono">{html.escape(job)}</span></div><div class="resource-meta views-timing-slot">{view_time}</div></div><div class="actions">{model_link}<a class="button" href="/jobs/{html.escape(job)}/transforms.json">transforms.json</a></div></div>{status_markup(job)}</section>{model_history_markup(job)}<section class="panel" id="views"><div class="panel-head"><h2>Perspective views · ชุดปัจจุบัน</h2><span class="badge">{len(images)} images</span></div>{pagination}<div class="gallery-grid">{"".join(cards) if cards else '<div class="empty">กำลังสร้าง views หรือยังไม่มีภาพ</div>'}</div>{pagination}</section>'''
    return layout(content, "jobs", info.get("label", job), "รายละเอียดและภาพที่ใช้สร้างโมเดล", message, level, dialogs)


def pose_preview_page(job: str, message="", level="ok"):
    directory = job_directory(job)
    if directory is None:
        return None
    info = job_manifest(job)
    model = model_file_for_job(job)
    replacements = {
        "__LABEL__": html.escape(info.get("label", job)),
        "__JOB__": html.escape(job, quote=True),
        "__JOB_URL__": "/job/" + quote(job, safe=""),
        "__POSE_URL__": "/poses/" + quote(job, safe=""),
        "__CONFIG__": json.dumps({"job": job}).replace("<", "\\u003c"),
        "__MODEL_LINK__": (f'<a class="button" href="/model/{quote(model.name, safe="")}">Model ล่าสุด</a>'
                           if model else "") + f'<a class="button" href="{models_url(job, views_status(job).get("run_id"))}">Models จากชุดนี้ ({sum(model_matches(model_info(path), job, views_status(job).get("run_id") or "legacy") for path in model_files_for_job(job))})</a>',
        "__LINEAGE__": lineage_markup(info),
        "__MESSAGE__": (f'<div class="warning" role="status" style="position:fixed;top:65px;left:15px;z-index:4;max-width:90vw">{html.escape(message)}</div>'
                        if message else ""),
    }
    page = (ROOT / "pose_viewer.html").read_text(encoding="utf-8")
    return re.sub(r"__(?:LABEL|JOB|JOB_URL|POSE_URL|CONFIG|MODEL_LINK|MESSAGE|LINEAGE)__",
                  lambda match: replacements[match.group(0)], page)


def source_preview_page(source: Path, source_url: str, label: str):
    name = html.escape(source.name)
    url = html.escape(source_url, quote=True)
    mime = html.escape(mimetypes.guess_type(source.name)[0] or "application/octet-stream", quote=True)
    if source.suffix.lower() in RAW_INSTA360:
        media = '''<div class="empty"><h3>ไฟล์ต้นฉบับจากกล้อง Insta360</h3>
        <p>ไฟล์ INSV/INSP เป็นภาพ dual-fisheye ที่ยังไม่ได้ stitch จึงยังสร้าง Gaussian Splat โดยตรงไม่ได้</p>
        <ol><li>เปิดไฟล์ด้วย Insta360 Studio</li><li>เลือก Export 360 Video ที่ความละเอียดและ bitrate สูงสุด</li>
        <li>ตรวจว่า MP4 ที่ได้เป็น equirectangular อัตราส่วน 2:1 แล้วอัปโหลดไฟล์นั้น</li></ol></div>'''
    elif source.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
        poster_url = html.escape(resource_preview_url('source', source_key_for_path(source)), quote=True)
        media = f'<video controls playsinline preload="metadata" poster="{poster_url}"><source src="{url}" type="{mime}">เบราว์เซอร์ไม่รองรับวิดีโอนี้</video>'
    else:
        media = f'<img src="{url}" alt="{name}">'
    key = source_key_for_path(source)
    jobs = jobs_for_source(key, source.name)
    linked_models = [model for model in sorted(MODELS.glob("*.ply"), key=lambda path:path.stat().st_mtime, reverse=True)
                     if model_matches(model_info(model), source=key)]
    button, dialog = source_generation_modal(source, key)
    job_rows = []
    for job in jobs:
        info = job_manifest(job)
        job_rows.append(f'''<article class="resource" data-job="{html.escape(job)}" data-source-hub="1" data-known-model="{html.escape((model_status(job) or {}).get("model") or "",quote=True)}"><div class="with-thumb">{resource_preview_markup('views', job, '/job/' + quote(job, safe=''))}<div><div class="resource-title"><a href="/job/{quote(job,safe='')}">{html.escape(info['label'])}</a></div><div class="resource-meta">{html.escape(job)} · {info['frames']} ภาพ Views · {len(model_files_for_job(job))} Models</div><div class="resource-meta">{timing_markup(views_status(job), 'Views')}</div></div></div><div>{status_markup(job)}</div><div class="actions"><a class="button" href="/job/{quote(job,safe='')}">เปิด Views / งาน</a><a class="button" href="/poses/{quote(job,safe='')}">กล้อง 3D</a><a class="button" href="{models_url(job)}">Models ทั้งหมด</a></div></article>''')
    model_rows = []
    for model in linked_models:
        info = model_info(model)
        job = info.get('job','')
        origin = f'<a href="/job/{quote(job,safe="")}">Views / งาน {html.escape(job)}</a>' if job_directory(job) else 'งานต้นทางถูกลบแล้ว'
        quality = info.get('quality',{})
        render_link = f'<a class="button" href="/quality/{quote(job,safe="")}/{quote(info["run_id"],safe="")}">ตรวจภาพ Render</a>' if quality_available(job, info.get('run_id')) else ''
        model_rows.append(f'''<article class="resource"><div class="with-thumb">{resource_preview_markup('model', model.name, '/model/' + quote(model.name, safe=''), info)}<div><div class="resource-title"><a href="/model/{quote(model.name,safe='')}">{html.escape(model.name)}</a></div><div class="resource-meta">{origin} · ชุด {html.escape(info.get('view_run_id') or 'เดิม (ไม่มีรหัส)')}</div></div></div><div class="resource-meta">{timing_markup(info,'Model')}</div><div class="actions"><a class="button primary" href="/model/{quote(model.name,safe='')}">เปิด 3D</a>{render_link}</div></article>''')
    content = f'''<nav class="lineage" aria-label="เส้นทาง Source Views Models"><a href="/sources">Sources ทั้งหมด</a><span>›</span><strong>{name}</strong><small>ไฟล์ต้นฉบับ → ชุด Views → Models ที่สร้างจากไฟล์นี้</small></nav><section class="stats"><a class="stat" href="#source"><strong>1</strong><span>ไฟล์ต้นฉบับ</span></a><a class="stat" href="#source-jobs"><strong>{len(jobs)}</strong><span>ชุด Views / งาน</span></a><a class="stat" href="#source-models"><strong>{len(linked_models)}</strong><span>Models</span></a></section><section class="panel" id="source"><div class="panel-head"><div><h2>{name}</h2>{source_resolution_markup(source)}<div class="muted">{html.escape(label)} · {format_bytes(source.stat().st_size)}</div></div>{button}</div><div class="media-view">{media}</div><p><a href="{url}" target="_blank" rel="noopener">เปิดไฟล์ต้นฉบับ</a></p></section><section class="panel" id="source-jobs"><div class="panel-head"><h2>Views / งานจากไฟล์นี้ ({len(jobs)})</h2><a href="/jobs?{html.escape(urlencode({'source':key}),quote=True)}">เปิดรายการ →</a></div><div class="list">{''.join(job_rows) or '<div class="empty">ยังไม่มี Views กดสร้างด้านบนเพื่อเริ่ม</div>'}</div></section><section class="panel" id="source-models"><div class="panel-head"><h2>Models จากไฟล์นี้ ({len(linked_models)})</h2><a href="{models_url(source=key)}">จัดการ Models →</a></div><div class="list">{''.join(model_rows) or '<div class="empty">ยังไม่มี Model</div>'}</div></section>'''
    return layout(content, "sources", source.name, "ต้นฉบับและผลงานทั้งหมดจากไฟล์นี้", extra_modals=dialog)


def experiments_page(batch=""):
    directory = EXPERIMENTS
    paths = sorted(directory.glob('*.json'), key=lambda path:path.stat().st_mtime, reverse=True)
    path = next((path for path in paths if path.stem == batch), None) if batch else (paths[0] if paths else None)
    report = read_json(path, {}) if path else {}
    reviews = read_json(directory / 'reviews' / path.name, {}) if path else {}
    cards, states = [], []
    active = False
    for record in report.get('cases', []):
        job = record.get('job','')
        exists = job_directory(job) is not None
        record_state = record.get('state','queued').lower()
        if record_state in {'preparing','training'}: record_state = 'running'
        status = (combined_job_status(job) if exists and record_state in {'queued','running'}
                  else record.get('status',{}))
        state = status.get('state', record_state)
        if state not in {'done','error'}:
            state = record_state
        states.append(state)
        active |= state in {'running','queued'}
        quality = status.get('quality', record.get('quality',{}))
        run = status.get('run_id') or record.get('run_id','')
        model_name = status.get('model') or record.get('model','')
        valid_model = bool(model_name and Path(model_name).name == model_name and (MODELS/model_name).is_file())
        origin_info = (model_info(MODELS/model_name) if valid_model else
                       {'job':job,'source_key':record.get('source_key') or 'data/raw:'+record.get('source','')})
        origin = source_link(origin_info)
        buttons = f'<a class="button" href="/job/{quote(job,safe="")}">Views / งาน</a><a class="button" href="/poses/{quote(job,safe="")}">กล้อง 3D</a>' if exists else ''
        if valid_model:
            buttons += f'<a class="button primary" href="/model/{quote(model_name,safe="")}">เปิด Model</a>'
        images = []
        if exists and re.fullmatch(r'\d{8}_\d{6}_[a-f0-9]{8}', run):
            run_dir = JOBS/job/'model_runs'/run
            if (run_dir/'3d.log').is_file():
                buttons += f'<a class="button" href="/jobs/{quote(job,safe="")}/model_runs/{run}/3d.log">Log</a>'
            renders = sorted((run_dir/'brush_export'/f'eval_{quality.get("training_steps",0)}').glob('*.png'))
            if renders:
                buttons += f'<a class="button" href="/quality/{quote(job,safe="")}/{run}">เทียบภาพจริง / Render</a>'
                for index in sorted({0, len(renders)//3, 2*len(renders)//3}):
                    url = '/jobs/'+quote(renders[index].relative_to(JOBS).as_posix(),safe='/')
                    images.append(f'<figure style="margin:0"><a href="{url}" target="_blank" rel="noopener"><img src="{url}" loading="lazy" style="width:100%;border-radius:8px" alt="ภาพ render จริงจาก Brush GPU"></a><figcaption class="resource-meta">{html.escape(renders[index].stem)}</figcaption></figure>')
        settings = record.get('settings',{})
        manifest = job_manifest(job) if exists else {}
        plan = record.get('plan') or manifest.get('capture_plan',{})
        profile = record.get('profile') or manifest.get('source_profile',{})
        info = f'{profile.get("width", "?")}×{profile.get("height", "?")} · {profile.get("fps", "?")} fps · ช่วง {plan.get("start_seconds",0):g}–{plan.get("end_seconds",0):g} วินาที · {plan.get("positions",0)} ตำแหน่ง / {plan.get("total_views",0)} Views'
        metrics = f'{quality.get("registered_panoramas",0)}/{quality.get("input_panoramas",plan.get("positions",0))} ตำแหน่งหา pose ได้ · {quality.get("training_steps",0):,} steps · PSNR masked {quality.get("eval_psnr_mean_db",0):.2f} dB · {html.escape(quality.get("compute_device",""))}' if quality.get('training_steps') else ''
        error = status.get('error') or record.get('error')
        error_html = f'<details><summary>ดูสาเหตุที่ยังสร้างไม่ได้</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">{html.escape(str(error))}</pre></details>' if error else ''
        expected = '<p class="muted">กรณีที่ต้องตรวจการเคลื่อนกล้องและวัตถุในฉากเพิ่มเติม สาเหตุที่ไฟล์อ่านไม่สำเร็จไม่ได้ยืนยันว่าขาด parallax</p>' if record.get('expectation') == 'insufficient_camera_motion' else ''
        review = reviews.get(record.get('id'), {})
        review_heading = 'ตรวจภาพ Render แล้ว' if review.get('images') else 'ผลตรวจสอบ'
        review_html = f'<p class="flash ok">{review_heading}: {html.escape(review.get("observation",""))}</p>' if review.get('observation') else ''
        reference = record.get('reference','')
        ref_link = f'<a href="{html.escape(reference,quote=True)}" target="_blank" rel="noopener">แหล่งวิดีโอและสิทธิ์การใช้</a>' if reference.startswith(('https://','http://')) else ''
        state_label = {'done':'สร้าง Model สำเร็จ','error':'สร้างไม่ได้','queued':'รอทดสอบ','running':'กำลังทดสอบ'}.get(state,state)
        if state == 'done' and not valid_model:
            state_label += ' · ไฟล์ Model ไม่อยู่แล้ว'
        cards.append(f'''<section class="panel"><div class="panel-head"><div><h2>{html.escape(record.get('label',record.get('name',record.get('source',''))))}</h2><p class="resource-meta">Source: {origin}</p></div><span class="badge {'good' if state=='done' and valid_model else 'bad' if state=='error' else 'running'}">{state_label}</span></div><p class="muted">{html.escape(info)}</p><div class="resource-meta">คุณภาพ {html.escape(settings.get('quality',''))} · {metrics}</div><p class="resource-meta">{timing_markup(status,'สร้าง Model')}</p>{expected}<div class="actions">{buttons}{ref_link}</div>{status_markup(status=status) if state=='running' else ''}<div class="pipeline" style="margin-top:16px">{''.join(images)}</div>{review_html}{error_html}</section>''')
    tabs = ''.join(f'<a class="button" href="/experiments?{urlencode({"batch":path.stem})}">{html.escape(path.stem)}</a>' for path in paths)
    content = f'''<div class="section-tabs">{tabs}</div><section class="stats"><div class="stat"><strong>{states.count('done')}</strong><span>สร้าง Model สำเร็จ</span></div><div class="stat"><strong>{states.count('error')}</strong><span>สร้างไม่ได้ / ต้องแก้ข้อมูล</span></div><div class="stat"><strong>{states.count('running')+states.count('queued')}</strong><span>กำลังทำ / รอทดสอบ</span></div></section><section class="panel"><h2>จากวิดีโอจริง ผ่าน pipeline ของแอป</h2><p class="muted">Extract → Perspective → COLMAP → Brush GPU โดยหา poses ใหม่เองทุกคลิป ภาพด้านล่างเป็น held-out render จาก Brush ก่อนกรอง PLY outlier ไม่ใช่ภาพหน้าจอ Three.js</p><p class="muted">ผลสำเร็จหมายถึงสร้างไฟล์และ render ได้ ควรเปิด 3D ตรวจรูปทรงและมุมใหม่ด้วย PSNR วัดเฉพาะพิกเซลที่ไม่ถูก mask และไม่รับรองคุณภาพทุกมุมหรือทุกวิดีโอ</p></section>{''.join(cards) or '<div class="empty">ยังไม่มีผลทดสอบ หรือไม่พบชุดทดสอบนี้</div>'}'''
    if report.get('purpose'):
        explanation = '<p class="muted">ลดความละเอียด Source โดยคงเส้นทาง 246 ตำแหน่ง, poses, depth seed/สีเริ่มต้น, masks และโปรไฟล์ Ultra เดิม เพื่อเทียบรายละเอียดภาพ ไม่ได้ทดสอบการหา pose ใหม่จากวิดีโอความละเอียดต่ำ</p>'
        content = content.replace('Extract → Perspective → COLMAP → Brush GPU โดยหา poses ใหม่เองทุกคลิป', 'Source → Perspective → poses เดิม → Brush GPU')
        content = content.replace('<h2>จากวิดีโอจริง ผ่าน pipeline ของแอป</h2>', '<h2>ทดลองความละเอียด Source · Concourse</h2>' + explanation)
        baseline = report.get('baseline', '')
        if Path(baseline).name == baseline and (MODELS / baseline).is_file():
            content = '<p><a class="button" href="/model/' + quote(baseline, safe='') + '">เปิด Concourse 6K ต้นแบบ</a></p>' + content
        comparison = report.get('comparison', {})
        if comparison.get('results'):
            rows = ''.join(f'<tr><td><a href="/model/{quote(item["model"],safe="")}">{item["source_width"]}×{item["source_height"]}</a></td><td>{item["crop_size"]}×{item["crop_size"]}</td><td>{item["common_reference_psnr_mean_db"]:.2f} dB</td></tr>' for item in comparison['results'])
            content = '<section class="panel"><h2>เทียบกับ Source 6K ชุดเดียวกัน</h2><p class="muted">232 มุมที่กันไว้ทดสอบ · resize Render เป็น 1536×1536 และใช้ masks เดิม ตัวเลขแต่ละรุ่นด้านล่างวัดกับ Source ของรุ่นนั้น จึงไม่ใช่คะแนนเปรียบเทียบเดียวกัน</p><table><thead><tr><th>Source</th><th>ภาพแต่ละมุม</th><th>PSNR อ้างอิง 6K</th></tr></thead><tbody>' + rows + '</tbody></table></section>' + content
    if active:
        content += '<script>setTimeout(()=>location.reload(),15000)</script>'
    return layout(content,'experiments','ผลทดสอบวิดีโอ 360 จริง',report.get('batch','ผลจากการสร้าง Model บนเครื่องนี้'))


class Handler(BaseHTTPRequestHandler):
    def send_data(self, data, content_type, code, cache_control):
        accepts_gzip = False
        for entry in self.headers.get('Accept-Encoding', '').split(','):
            parts = entry.lower().strip().split(';')
            if parts[0] != 'gzip':
                continue
            quality = 1.0
            for parameter in parts[1:]:
                key, _, value = parameter.strip().partition('=')
                if key == 'q':
                    try:
                        quality = float(value)
                    except ValueError:
                        quality = 0.0
            accepts_gzip |= 0 < quality <= 1
        compressed = accepts_gzip and len(data) > 4096
        if compressed:
            data = gzip.compress(data, compresslevel=3, mtime=0)
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', cache_control)
        self.send_header('Vary', 'Accept-Encoding')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # A view switch may cancel its pending metric request.

    def send_html(self, body, code=200):
        self.send_data(body.encode(), 'text/html; charset=utf-8', code, 'private, no-cache')

    def send_json(self, value, code=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_data(data, 'application/json; charset=utf-8', code, 'no-store')

    def redirect_to(self, location="/", message="", level="ok"):
        if not location.startswith("/") or location.startswith("//"):
            location = "/"
        if message:
            separator = "&" if "?" in location else "?"
            location += separator + urlencode({"message": message, "level": level})
        if self.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return self.send_json({'ok':level != 'error', 'message':message, 'redirect_to':location},
                                  400 if level == 'error' else 200)
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    @staticmethod
    def settings_from_form(form, defaults=None):
        defaults = defaults or DEFAULT_VIEW_SETTINGS
        return normalized_settings({
            key: form.getfirst(key, defaults.get(key, DEFAULT_VIEW_SETTINGS[key])) for key in DEFAULT_VIEW_SETTINGS
        })

    def send_file(self, target: Path, cache_control=None):
        """Serve a file without loading a video into memory, including single-range video requests."""
        stat = target.stat()
        size = stat.st_size
        etag = f'"{stat.st_mtime_ns:x}-{size:x}"'
        if cache_control is None:
            cache_control = 'private, no-cache'
            if target.is_relative_to(ROOT / 'assets' / 'vendor'):
                cache_control = 'public, max-age=31536000, immutable'
        if not self.headers.get('Range') and etag in self.headers.get('If-None-Match', '').split(', '):
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', cache_control)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        start, end, code = 0, size - 1, 200
        requested_range = self.headers.get("Range", "").strip()
        if requested_range:
            unit, marker, values = requested_range.partition("=")
            first, dash, last = values.partition("-")
            try:
                if unit != "bytes" or not marker or not dash or "," in values:
                    raise ValueError
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                elif last:
                    suffix = int(last)
                    if suffix <= 0:
                        raise ValueError
                    start = max(size - suffix, 0)
                else:
                    raise ValueError
                if start < 0 or start >= size or end < start:
                    raise ValueError
                code = 206
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header('ETag', etag)
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        elif target.suffix.lower() in {".mjs", ".js", ".json"}:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(length))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with target.open("rb") as file:
                file.seek(start)
                remaining = length
                while remaining:
                    chunk = file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        parsed = urlparse(self.path); path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        message = query.get("message", [""])[0]
        level = query.get("level", ["ok"])[0]
        if path == "/":
            return self.send_html(dashboard_page(message, level))
        if path == "/tutorial":
            from tutorial import tutorial_content
            from view_suggestions import PRESETS
            return self.send_html(layout(tutorial_content(PRESETS, QUALITY_PROFILES), "tutorial", "Tutorial", "Record → Upload → Views → Camera Preview → Model"))
        if path.startswith('/assets/tutorial/'):
            name = path.removeprefix('/assets/tutorial/')
            if name not in {'dji-view.jpg', 'dji-poses.svg'}:
                return self.send_html('asset not found', 404)
            return self.send_file(ROOT / 'assets' / 'tutorial' / name)
        if path == "/sources":
            return self.send_html(sources_page(message, level))
        if path == "/experiments":
            return self.send_html(experiments_page(query.get("batch", [""])[0]))
        if path == "/benchmarks":
            return self.send_html(layout(render_benchmark_report(build_benchmark_report(JOBS), query),
                                         active="benchmarks", title="เทียบ GPU / Insta360 Cloud",
                                         subtitle="ค่าประมาณ · ผลจริง RX 6700 XT · ระบบ auto"))
        if path == "/jobs":
            return self.send_html(jobs_page(message, level, query.get("source", [""])[0]))
        if path == "/models":
            return self.send_html(models_page(message, level, query.get("job", [""])[0], query.get("view_run", [None])[0], query.get("source", [""])[0]))
        if path.startswith('/compression/'):
            page = compression_page(path.removeprefix('/compression/'), message, level)
            return self.send_html(page or 'compression record not found', 200 if page else 404)
        if path.startswith('/api/compression/'):
            record = compression_record(path.removeprefix('/api/compression/'))
            return self.send_json(record or {'error':'compression record not found'}, 200 if record else 404)
        if path.startswith('/api/quality-metrics/'):
            parts = path.split('/')
            if len(parts) != 5:
                return self.send_json({'error': 'comparison not found'}, 404)
            try:
                catalog = quality_frames(parts[3], parts[4])
                frame = next((item for item in catalog[1] if item['name'] == query.get('frame', [''])[0]), None) if catalog else None
                if frame is None:
                    return self.send_json({'error': 'comparison view not found'}, 404)
                return self.send_json(frame_metrics(frame))
            except (ValueError, OSError, cv2.error) as exc:
                return self.send_json({'error': str(exc)}, 422)
        if path.startswith('/compressed-download/'):
            identifier = path.removeprefix('/compressed-download/').removesuffix('.spz')
            record = compression_record(identifier)
            if record and record.get('state') == 'ready':
                target = compression_directory(identifier) / 'model.spz'
                if target.is_file() and target.stat().st_size == record.get('bytes'):
                    return self.send_file(target, cache_control='public, max-age=31536000, immutable')
            return self.send_html('compressed model not found', 404)
        if path.startswith('/compression-comparison/'):
            identifier = path.removeprefix('/compression-comparison/').removesuffix('.jpg')
            record = compression_record(identifier)
            if record:
                target = compression_directory(identifier) / 'comparison.jpg'
                if target.is_file():
                    return self.send_file(target)
            return self.send_html('comparison not found', 404)
        if path == '/api/resource-preview':
            try:
                paths, mode, _ = resource_preview_assets(query.get('kind', [''])[0], query.get('key', [''])[0])
                target = thumbnail_file(paths, mode, PREVIEWS)
                return self.send_file(target, cache_control='private, no-cache')
            except (ValueError, OSError, TypeError) as exc:
                return self.send_json({'error': str(exc)}, 404)
        if path.startswith("/model-inputs/"):
            name = path.removeprefix("/model-inputs/")
            if Path(name).name != name or not (MODELS / name).is_file() or not name.endswith('.ply'):
                return self.send_html("model not found", 404)
            return self.send_html(model_inputs_page(MODELS / name, query.get('page', [1])[0]))
        if path == "/api/view-suggestions":
            try:
                result = source_view_suggestions(
                    query.get("source", [""])[0], query.get("quality", [None])[0],
                    query.get("projection", [None])[0], query.get("fov", [None])[0],
                )
                return self.send_json(result)
            except (ValueError, OSError, TypeError) as exc:
                return self.send_json({"error": str(exc)}, 422)
        if path.startswith('/api/job-config/'):
            try:
                return self.send_json(job_config(unquote(path[len('/api/job-config/'):])) )
            except ValueError as exc:
                return self.send_json({'error': str(exc)}, 404)
        if path.startswith('/api/model-config/'):
            try:
                return self.send_json(model_config(unquote(path[len('/api/model-config/'):])) )
            except ValueError as exc:
                return self.send_json({'error':str(exc)},404)
        if path in {"/api/capture-plan", "/api/capture-image"}:
            try:
                source_key = query.get("source", [""])[0]
                source = source_from_key(source_key)
                if source is None:
                    raise ValueError("ไม่พบไฟล์ต้นฉบับ")
                profile = probe_source(source)
                settings = normalized_settings({key: query[key][0] for key in DEFAULT_VIEW_SETTINGS if key in query})
                plan = sampling_plan(profile, settings)
                ordinal = int(query.get("ordinal", [0])[0])
                direction = int(query.get("direction", [0])[0])
                if path == "/api/capture-plan":
                    ordinal = max(0, min(ordinal, (plan["positions"] or 1) - 1))
                    source_kind = "raw" if source.parent == RAW else "uploads"
                    return self.send_json({"plan": plan, "profile": profile, "settings": settings,
                        "ordinal": ordinal, "source_url": source_route(source_kind, source)})
                data, _, frame_index = preview_jpeg(source, profile, settings, ordinal, direction,
                    max(64, min(512, int(query.get("size", [512])[0]))), query.get("raw", ["0"])[0] == "1")
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "private, max-age=60")
                self.send_header("X-Source-Frame", str(frame_index))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            except (ValueError, OSError, TypeError) as exc:
                return self.send_json({"error": str(exc)}, 422)
        if path == "/assets/pose_viewer.mjs":
            return self.send_file(ROOT / "pose_viewer.mjs")
        if path == "/assets/view_settings.mjs":
            return self.send_file(ROOT / "view_settings.mjs")
        if path == "/assets/capture_preview.mjs":
            return self.send_file(ROOT / "capture_preview.mjs")
        if path in {'/assets/model_viewer.mjs', '/assets/model_navigation.mjs', '/assets/model_gamepad.mjs', '/assets/model_minimap.mjs', '/assets/editor.css', '/assets/editor_ui.mjs', '/assets/quality_viewer.mjs', '/assets/action_feedback.mjs', '/assets/tutorial.mjs'}:
            return self.send_file(ROOT / Path(path).name)
        if path.startswith('/assets/vendor/'):
            directory = (ROOT / 'assets/vendor').resolve()
            target = (directory / path.removeprefix('/assets/vendor/')).resolve()
            if not target.is_relative_to(directory):
                return self.send_html('bad asset path', 400)
            if target.is_file():
                return self.send_file(target)
            return self.send_html('viewer asset not installed', 404)
        if path.startswith('/api/model-cameras/'):
            try:
                return self.send_json(model_camera_data(path.removeprefix('/api/model-cameras/')))
            except (ValueError, OSError, TypeError) as exc:
                return self.send_json({'error': str(exc)}, 422)
        if path.startswith("/quality/"):
            parts = path.split("/")
            page = model_quality_page(parts[2], parts[3]) if len(parts) == 4 else None
            return self.send_html(page if page else "not found", 200 if page else 404)
        if path.startswith("/poses/"):
            job = path.removeprefix("/poses/")
            page = pose_preview_page(job, message, level)
            return self.send_html(page if page else "not found", 200 if page else 404)
        if path.startswith("/api/poses/"):
            job = path.removeprefix("/api/poses/")
            directory = job_directory(job)
            if directory is None:
                return self.send_json({"error": "ไม่พบ job"}, 404)
            try:
                preview = available_pose_preview(job)
                status = read_json(directory / "pose_status.json", {})
                busy = task_is_active(job)
                if not preview and busy:
                    status = combined_job_status(job)
                return self.send_json({
                    "state": "ready" if preview else "running" if busy else status.get("state", "waiting"),
                    "preview": preview or None, "status": status,
                    "busy": busy,
                    "training_ready": bool(cached_pose_preview(directory)),
                })
            except (OSError, ValueError, KeyError) as exc:
                return self.send_json({"error": str(exc)}, 422)
        if path.startswith("/preview/"):
            parts = path.split("/", 3)
            if len(parts) != 4:
                return self.send_html("not found", 404)
            source = source_from_route(parts[2], parts[3])
            if source is None:
                return self.send_html("not found", 404)
            label = "data/raw" if parts[2] == "raw" else "web_data/uploads"
            return self.send_html(source_preview_page(source, source_route(parts[2], source), label))
        if path.startswith("/source/"):
            parts = path.split("/", 3)
            if len(parts) != 4:
                return self.send_html("not found", 404)
            source = source_from_route(parts[2], parts[3])
            if source is None:
                return self.send_html("not found", 404)
            return self.send_file(source)
        if path.startswith("/api/job/"):
            job = Path(path.split("/", 3)[3]).name
            if not (JOBS / job).is_dir():
                return self.send_json({"state": "missing", "progress": 0}, 404)
            return self.send_json({**combined_job_status(job), "views": task_estimated_status(job, views_status(job)),
                                   "busy": task_is_active(job)})
        if path.startswith("/api/run/"):
            parts = path.split("/")
            if len(parts) != 5 or job_directory(parts[3]) is None or not re.fullmatch(r"\d{8}_\d{6}_[a-f0-9]{8}", parts[4]):
                return self.send_json({"error": "ไม่พบรอบสร้าง model"}, 404)
            status = read_json(JOBS / parts[3] / "model_runs" / parts[4] / "status.json", {})
            return self.send_json(task_estimated_status(parts[3], live_training_status(parts[3], status)), 200 if status else 404)
        if path.startswith("/model/"):
            model = Path(path.split("/", 2)[2]).name
            if model not in {x.name for x in MODELS.glob("*.ply")}:
                return self.send_html("model not found", 404)
            compression = query.get('compression', [None])[0]
            if compression:
                record = compression_record(compression)
                if (not record or record.get('state') != 'ready' or record.get('parent_model') != model
                        or record.get('source') != source_signature(MODELS / model)
                        or not (compression_directory(compression) / 'model.spz').is_file()):
                    return self.send_html('compressed model does not belong to this model', 404)
            viewer_config = json.dumps(model_viewer_config(model), ensure_ascii=False)
            info = model_info(MODELS / model)
            render_link = f'<a href="/quality/{quote(info["job"],safe="")}/{quote(info["run_id"],safe="")}">เทียบภาพจริง / Render</a>' if quality_available(info.get('job'), info.get('run_id')) else ''
            sibling_options = []
            for other in sorted(MODELS.glob("*.ply"), key=lambda item:item.stat().st_mtime, reverse=True):
                if info.get("job") and model_matches(model_info(other), info["job"], info.get("view_run_id") or "legacy"):
                    sibling_options.append(f'<option value="/model/{quote(other.name,safe="")}"{" selected" if other.name == model else ""}>{html.escape(other.name)}</option>')
            siblings = f'<label for="sibling-model">Model จาก Views ชุดนี้ ({len(sibling_options)})</label><select id="sibling-model">{"".join(sibling_options)}</select>' if sibling_options else ''
            return self.send_html(model_viewer_page(model, info, siblings, render_link, query.get('renderer', ['spark'])[0], query.get('variant', [None])[0], compression))
        if path.startswith('/model-fast/'):
            name = unquote(path.removeprefix('/model-fast/'))
            try:
                directory = variant_directory(name)
                source = MODELS / name
                fast = available_variant(source)
                if fast:
                    cache = 'public, max-age=31536000, immutable' if query.get('v', [''])[0] == fast['sha256'][:16] else 'no-store'
                    return self.send_file(directory / 'fast.spz', cache_control=cache)
            except (ValueError, OSError):
                pass
            return self.send_html('fast model not found', 404)
        if path.startswith("/jobs/") or path.startswith("/models/"):
            parts = Path(path.lstrip("/")).parts
            base = JOBS if parts[0] == "jobs" else MODELS
            target = (base.joinpath(*parts[1:])).resolve()
            if base.resolve() not in target.parents and target != base.resolve(): return self.send_html("bad path", 400)
            if target.is_file():
                return self.send_file(target)
        if path.startswith("/job/"):
            job = Path(path.split("/", 2)[2]).name
            detail = job_detail_page(job, message, level, query.get('page', [1])[0])
            if detail:
                return self.send_html(detail)
        self.send_html("not found", 404)

    def do_POST(self):
        if urlparse(self.path).path not in {"/", "/actions"}:
            return self.send_html("not found", 404)
        return_to = "/"
        try:
            form = cgi.FieldStorage(
                fp=self.rfile, headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type", "")},
            )
            action = form.getfirst("action", "")
            return_to = form.getfirst("return_to", "/")
            if action == 'compress_model':
                name = form.getfirst('model', '')
                if Path(name).name != name or not name.endswith('.ply') or not (MODELS/name).is_file():
                    raise ValueError('ไม่พบ Model ต้นทาง')
                identifier = start_fast_variant(MODELS / name)
                return self.redirect_to('/compression/' + identifier, 'เปิด record ของ Model บีบอัดแล้ว')
            if action == "upload_source":
                item = form["file"] if "file" in form else None
                if item is None or not getattr(item, "filename", None):
                    raise ValueError("กรุณาเลือกไฟล์ source")
                name = safe_name(item.filename)
                if Path(name).suffix.lower() not in ALLOWED:
                    raise ValueError("รองรับ JPG, PNG, WEBP, MP4, MOV, MKV, WEBM, INSV และ INSP")
                if item.done == -1:
                    raise ValueError('อัปโหลดไม่ครบ ไฟล์ยังไม่ถูกเพิ่ม กรุณาลองใหม่')
                target = publish_upload(UPLOADS, name, item.file)
                return self.redirect_to(return_to, f"อัปโหลด {target.name} แล้ว")

            if action == "upload_model":
                item = form["model"] if "model" in form else None
                if item is None or not getattr(item, "filename", None):
                    raise ValueError("กรุณาเลือกไฟล์ model")
                name = safe_name(item.filename)
                if Path(name).suffix.lower() != ".ply":
                    raise ValueError("model ต้องเป็นไฟล์ .ply")
                if item.done == -1:
                    raise ValueError('อัปโหลดไม่ครบ ไฟล์ยังไม่ถูกเพิ่ม กรุณาลองใหม่')
                def validate_ply(path):
                    with path.open('rb') as uploaded:
                        header = uploaded.read(5)
                    if not (header.startswith(b"ply\n") or header.startswith(b"ply\r\n")):
                        raise ValueError("ไฟล์นี้ไม่มี PLY header")
                target = publish_upload(MODELS, name, item.file, validate_ply)
                write_json(model_metadata_path(target.name), {
                    "model": target.name, "uploaded_at": utc_now(),
                    "recorded_at": file_timestamp(target), "viewer_config": {},
                })
                return self.redirect_to(return_to, f"อัปโหลด model {target.name} แล้ว")

            if action == "generate":
                source_key = form.getfirst("source", "")
                source = source_from_key(source_key)
                if source is None:
                    raise ValueError("ไม่พบ source ต้นฉบับ")
                defaults = source_view_suggestions(source_key, form.getfirst("quality", None),
                                                   form.getfirst("projection", None))["settings"]
                settings = self.settings_from_form(form, defaults)
                plan = sampling_plan(probe_source(source), settings)
                auto_model = form.getfirst("auto_model", "") == "1"
                if auto_model and (plan["positions"] or 0) < 6:
                    raise ValueError("เลือกอย่างน้อย 6 เฟรมจากหลายตำแหน่งเพื่อสร้างโมเดล หรือปิดสร้างโมเดลอัตโนมัติเพื่อดู Views ก่อน")
                job, label = create_job_record(source, source_key, settings, form.getfirst('label', ''))
                if not start_views(job, source_key, settings, auto_model=auto_model):
                    raise ValueError('งานใหม่ยังเริ่มไม่ได้ กรุณาตรวจสถานะในหน้า Jobs')
                message = "เริ่มสร้าง Views → Camera Pose → Gaussian Splat อัตโนมัติแล้ว" if auto_model else "เริ่มสร้าง Views แล้ว"
                return self.redirect_to(f"/job/{job}", f'{label} · {message}')

            if action == "rename_source":
                source_key = form.getfirst("source", "")
                source = source_from_key(source_key)
                if source is None:
                    raise ValueError("ไม่พบ source")
                linked = jobs_for_source(source_key, source.name)
                if any(task_is_active(job) for job in linked):
                    raise ValueError("เปลี่ยนชื่อไม่ได้ขณะที่มี job กำลังทำงาน")
                new_name = safe_name(form.getfirst("new_name", ""))
                if Path(new_name).suffix.lower() != source.suffix.lower():
                    raise ValueError("นามสกุลไฟล์ต้องเหมือนเดิม")
                target = source.with_name(new_name)
                if target.exists() and target != source:
                    raise ValueError("มี source ชื่อนี้แล้ว")
                source.rename(target)
                new_key = source_key_for_path(target)
                for job in linked:
                    info = job_manifest(job)
                    info.update({"source_name": target.name, "source_key": new_key})
                    save_job_manifest(job, {key: info[key] for key in ("label", "source_name", "source_key", "settings")})
                    transforms = JOBS / job / "transforms.json"
                    if transforms.is_file():
                        meta = read_json(transforms, {})
                        meta.update({"source_name": target.name, "source_key": new_key})
                        write_json(transforms, meta)
                snapshots = list(MODELS.glob("*.meta.json"))
                for job in linked:
                    snapshots.extend((JOBS / job).glob("model_runs/*/status.json"))
                    snapshots.extend((JOBS / job).glob("view_runs/*/status.json"))
                    snapshots.extend([JOBS / job / "3d_status.json", JOBS / job / "views_status.json"])
                for snapshot in snapshots:
                    record = read_json(snapshot, {})
                    if record.get("source_key") == source_key or (record.get("job") in linked and not record.get("source_key")):
                        record.update(source_name=target.name, source_key=new_key)
                        if record.get("source_profile"):
                            record['source_profile']['name'] = target.name
                        write_json(snapshot, record)
                return self.redirect_to(return_to, f"เปลี่ยนชื่อเป็น {target.name} แล้ว")

            if action == "delete_source":
                source_key = form.getfirst("source", "")
                source = source_from_key(source_key)
                if source is None:
                    raise ValueError("ไม่พบ source")
                linked = jobs_for_source(source_key, source.name)
                cascade = form.getfirst("cascade", "") == "1"
                if linked and not cascade:
                    raise ValueError(f"source นี้มี {len(linked)} jobs กรุณาเลือกให้ลบ jobs/models ด้วย")
                if any(task_is_active(job) for job in linked):
                    raise ValueError("ลบไม่ได้ขณะที่มี job กำลังทำงาน")
                if cascade:
                    for job in linked:
                        for model in model_files_for_job(job):
                            delete_model_file(model.name)
                        directory = job_directory(job)
                        if directory:
                            shutil.rmtree(directory)
                source.unlink()
                return self.redirect_to(return_to, f"ลบ source {source.name} แล้ว")

            if action in {"update_job", "reprocess_job"}:
                job = form.getfirst("job", "")
                directory = job_directory(job)
                if directory is None:
                    raise ValueError("ไม่พบ job")
                if task_is_active(job):
                    raise ValueError("job นี้กำลังทำงาน")
                info = job_manifest(job)
                settings = self.settings_from_form(form)
                info["settings"] = settings
                with JOB_NAMING_LOCK:
                    info['label'] = suggested_job_label(clean_label(form.getfirst('label', ''), job), exclude=job)
                    save_job_manifest(job, {key: info[key] for key in ("label", "source_name", "source_key", "settings")})
                meta_file = directory / "transforms.json"
                if meta_file.is_file():
                    meta = read_json(meta_file, {})
                    meta["job_label"] = info["label"]
                    write_json(meta_file, meta)
                if action == "reprocess_job":
                    if not info.get("source_key") or source_from_key(info["source_key"]) is None:
                        raise ValueError("ไม่พบ source ต้นฉบับสำหรับสร้าง views ใหม่")
                    source = source_from_key(info["source_key"])
                    sampling_plan(probe_source(source), settings)
                    start_views(job, info["source_key"], settings, reprocess=True,
                                auto_model=form.getfirst("auto_model", "") == "1")
                    return self.redirect_to(return_to, f"กำลังสร้าง views ของ {info['label']} ใหม่")
                return self.redirect_to(return_to, f"บันทึกชื่อ job {info['label']} แล้ว")

            if action in {"generate3d", "estimate_poses"}:
                job = form.getfirst("job", "")
                directory = job_directory(job)
                if directory is None or not (directory / "transforms.json").is_file():
                    raise ValueError("job ยังไม่มี perspective views")
                poses_only = action == "estimate_poses"
                selected_frames = None
                signature = form.getfirst("selection_signature", None)
                if not poses_only and form.getfirst("selected_frames", ""):
                    selected_frames = validate_selection(cached_pose_preview(directory),
                        json.loads(form.getfirst("selected_frames")), signature)
                if not start_3d(job, poses_only=poses_only, selected_frames=selected_frames, selection_signature=signature):
                    raise ValueError("job นี้กำลังทำงานอยู่")
                step = "หา Camera Pose (ยังไม่ train)" if poses_only else "Generate 3D"
                return self.redirect_to(return_to, f"เริ่ม {step} สำหรับ {job} แล้ว")

            if action == 'refine_model':
                model = form.getfirst('model', '')
                info, _, _ = refinement_input(model)
                if not start_refinement(model):
                    raise ValueError('งานนี้กำลังทำงานอยู่ รอให้รอบปัจจุบันเสร็จก่อน')
                return self.redirect_to('/job/' + quote(info['job'], safe=''), 'เริ่มปรับละเอียดจาก checkpoint เดิมแล้ว · ผลจะเป็น Model รุ่นใหม่')

            if action == "delete_job":
                job = form.getfirst("job", "")
                directory = job_directory(job)
                if directory is None:
                    raise ValueError("ไม่พบ job")
                if task_is_active(job):
                    raise ValueError("ลบไม่ได้ขณะที่ job กำลังทำงาน")
                if form.getfirst("delete_model", "") == "1":
                    for model in model_files_for_job(job):
                        delete_model_file(model.name)
                else:
                    archive_existing_models(job)
                shutil.rmtree(directory)
                return self.redirect_to(return_to, f"ลบ job {job} แล้ว")

            if action == "rename_model":
                name = Path(form.getfirst("model", "")).name
                source = MODELS / name
                if not name or not source.is_file():
                    raise ValueError("ไม่พบ model")
                new_name = safe_name(form.getfirst("new_name", ""))
                if Path(new_name).suffix.lower() != ".ply":
                    raise ValueError("ชื่อ model ต้องลงท้าย .ply")
                target = MODELS / new_name
                if target.exists() and target != source:
                    raise ValueError("มี model ชื่อนี้แล้ว")
                rename_model_file(name, target.name)
                return self.redirect_to(return_to, f"เปลี่ยนชื่อ model เป็น {target.name} แล้ว")

            if action == "delete_model":
                name = Path(form.getfirst("model", "")).name
                target = MODELS / name
                if not name or not target.is_file():
                    raise ValueError("ไม่พบ model")
                delete_model_file(name)
                return self.redirect_to(return_to, f"ลบ model {name} แล้ว")

            raise ValueError("ไม่รู้จัก action นี้")
        except Exception as exc:
            return self.redirect_to(return_to, str(exc), "error")

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def find_port(preferred=8000):
    for port in range(preferred, preferred + 100):
        with socket.socket() as sock:
            try: sock.bind(("127.0.0.1", port)); return port
            except OSError: pass
    raise RuntimeError("No free port found")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--view-device", choices=("auto", "cpu", "wgpu"), default=None)
    ap.add_argument("--colmap-device", choices=("auto", "cpu", "cuda"), default=None)
    ap.add_argument("--mask-device", default=None, help="auto, cpu, cuda[:N], or directml[:N]")
    ap.add_argument("--gpu-index", type=int, default=None, help="COLMAP GPU index; -1 selects automatically")
    args = ap.parse_args()
    if args.view_device is not None:
        os.environ["VIEW_DEVICE"] = args.view_device
    if args.colmap_device is not None:
        os.environ["COLMAP_DEVICE"] = args.colmap_device
    if args.mask_device is not None:
        if not re.fullmatch(r"auto|cpu|cuda(?::\d+)?|directml(?::\d+)?", args.mask_device):
            ap.error("--mask-device must be auto, cpu, cuda[:N], or directml[:N]")
        os.environ["MASK_DEVICE"] = args.mask_device
    if args.gpu_index is not None:
        if args.gpu_index < -1:
            ap.error("--gpu-index must be -1 or nonnegative")
        os.environ["COLMAP_GPU_INDEX"] = str(args.gpu_index)
    for record in compression_records():
        if record.get('state') in {'queued', 'running'} and not task_lock_alive(compression_directory(record['id']) / 'record.json'):
            record.update(state='error', error='Compression was interrupted; use the compression button to retry.', finished_at=utc_now())
            write_record_json(compression_directory(record['id']) / 'record.json', record)
    port = find_port(args.port); server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"360 web app: http://127.0.0.1:{port}", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
