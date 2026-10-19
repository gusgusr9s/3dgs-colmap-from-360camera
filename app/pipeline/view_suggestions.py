"""Explainable view settings from source metadata and successful local runs."""

from app.paths import ROOT, WORKSPACE

from copy import deepcopy
from functools import lru_cache
import hashlib
import math
import json
import uuid
from pathlib import Path

import cv2

from app.pipeline.sampling_policy import recommended_sample_every


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
PROFILE_CACHE = WORKSPACE / 'web_data' / 'source_profiles'
PRESETS = {
    "preview": {"resolution": 640, "video_resolution": 960, "gap": .30, "overlap": .30},
    "high": {"resolution": 1024, "video_resolution": 1536, "gap": .20, "overlap": .50},
    "ultra": {"resolution": 1536, "video_resolution": 2048, "gap": .15, "overlap": .50},
}


def positive_number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError):
        return None


def probe_source(source: Path):
    stat = source.stat()
    return deepcopy(_probe_cached(str(source.resolve()), stat.st_size, stat.st_mtime_ns))


@lru_cache(maxsize=128)
def _probe_cached(filename, size, modified_ns):
    source = Path(filename)
    if source.suffix.lower() in {".insv", ".insp"}:
        raise ValueError("กรุณา Export ไฟล์ดิบเป็น 360 MP4 ด้วย Insta360 Studio ก่อน")
    cache_key = hashlib.sha256(repr(('v1', filename, size, modified_ns)).encode()).hexdigest()
    cache_file = PROFILE_CACHE / (cache_key + '.json')
    try:
        cached = json.loads(cache_file.read_text(encoding='utf8'))
        if (cached.get('name') == source.name and cached.get('file_size') == size
                and cached.get('modified_ns') == modified_ns and cached.get('width', 0) > 0
                and cached.get('height', 0) > 0 and cached.get('fingerprint')):
            return cached
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    image = source.suffix.lower() in IMAGE_SUFFIXES
    fps, frame_count = None, 1 if image else None
    if image:
        decoded = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("อ่านภาพต้นฉบับไม่ได้")
        height, width = decoded.shape[:2]
    else:
        capture = cv2.VideoCapture(str(source))
        try:
            if not capture.isOpened():
                raise ValueError("อ่านวิดีโอต้นฉบับไม่ได้")
            fps = positive_number(capture.get(cv2.CAP_PROP_FPS))
            count = positive_number(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            frame_count = int(count) if count else None
            # Decode one frame to confirm dimensions rather than trusting headers.
            ok, decoded = capture.read()
            if not ok or decoded is None:
                raise ValueError("ไม่พบเฟรมที่อ่านได้ในวิดีโอ")
            height, width = decoded.shape[:2]
        finally:
            capture.release()
    fingerprint = hashlib.sha256(str(size).encode())
    with source.open("rb") as handle:
        fingerprint.update(handle.read(65536))
        if size > 65536:
            handle.seek(max(65536, size - 65536))
            fingerprint.update(handle.read(65536))
    panorama = abs(width / height - 2.0) < .08
    profile = {
        "name": source.name, "kind": "image" if image else "video",
        "width": width, "height": height, "fps": fps, "frame_count": frame_count,
        "duration_seconds": frame_count / fps if not image and frame_count and fps else None,
        "aspect_ratio": round(width / height, 5),
        "detected_projection": "panorama" if panorama else "walkthrough",
        "fingerprint": fingerprint.hexdigest(), "file_size": size,
        "modified_ns": modified_ns,
    }
    temporary = PROFILE_CACHE / (uuid.uuid4().hex + '.tmp')
    try:
        PROFILE_CACHE.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(profile), encoding='utf8')
        temporary.replace(cache_file)
    except OSError:
        pass  # Metadata discovery still works on a read-only installation.
    finally:
        temporary.unlink(missing_ok=True)
    return profile


def view_estimate(profile, settings):
    panorama = settings["projection"] == "panorama" or (
        settings["projection"] == "auto" and profile["detected_projection"] == "panorama")
    requested = max(1, int(settings["sample_every"]))
    fps = positive_number(profile.get("fps"))
    limit = max(1, int(fps * .3)) if fps and profile["kind"] == "video" else requested
    interval = requested if settings.get('sampling_guard') == 'manual' else min(requested, limit)
    count = profile.get("frame_count")
    positions = math.ceil(count / interval) if count else None
    horizontal = int(settings["views"]) if panorama else 1
    vertical = 2 * max(4, math.ceil(horizontal / 2)) if panorama and settings["quality"] != "preview" else 0
    return {
        "effective_sample_every": interval,
        "sample_gap_seconds": interval / fps if fps and profile["kind"] == "video" else None,
        "positions": positions, "horizontal_views": horizontal, "vertical_views": vertical,
        "views_per_frame": horizontal + vertical,
        "total_views": positions * (horizontal + vertical) if positions else None,
    }


def suggest_view_settings(profile, history=(), quality=None, projection=None, fov=None):
    records = [item for item in history if item.get("settings") and item.get("level", -1) >= 0]
    def rank(item):
        return (item.get("level", 0), item.get("coverage", 0), item.get("psnr", 0), item.get("recorded_at", ""))
    projection = projection if projection in {"panorama", "walkthrough"} else profile["detected_projection"]
    if quality is None:
        completed = [item for item in records if item.get('level') == 2
                     and item['settings'].get('quality') in PRESETS
                     and item['settings'].get('projection', 'auto') in {projection, 'auto'}]
        successful = max(completed, key=rank, default=None)
        quality = successful['settings']['quality'] if successful else 'high'
    quality = quality if quality in PRESETS else 'high'
    matches = [item for item in records if item["settings"].get("quality", "high") == quality
               and item["settings"].get("projection", "auto") in {projection, "auto"}]
    selected = max(matches, key=rank, default=None)
    proven = selected if selected and selected.get("level", 0) > 0 else None
    old = selected["settings"] if selected else {}
    preset = PRESETS[quality]
    angle = positive_number(fov) or (positive_number(old.get("fov")) if proven else None) or 90
    angle = max(30, min(150, angle))
    reasons = {
        "quality": "ใช้คุณภาพ " + quality + " เพื่อกำหนดความละเอียดและมุมเงย/ก้ม",
        "projection": ("ตรวจสัดส่วนใกล้ 2:1 จึงแนะนำภาพ 360; เปลี่ยนได้ถ้าเป็นวิดีโอปกติ"
                       if profile["detected_projection"] == "panorama" else "สัดส่วนไม่ใช่ 2:1 จึงเก็บเฟรมเป็นภาพวิดีโอปกติ"),
        "fov": "เริ่มที่ 90° สำหรับภาพที่ไม่มีข้อมูล FOV กล้อง",
    }
    if projection != profile["detected_projection"]:
        reasons["projection"] = "ใช้ชนิดภาพที่คุณเลือก"
    if projection == "panorama":
        # Angular pixel coverage is a practical ceiling, not a camera calibration.
        available = profile["width"] * angle / 360
        side = max(160, min(preset["resolution"], int(available / 64) * 64))
        width = height = side
        reasons["width"] = reasons["height"] = (
            f"คำนวณจากภาพ 360 กว้าง {profile['width']} px ที่ FOV {angle:g}° และเพดานคุณภาพ {quality}"
        )
        views = max(6, min(16, math.ceil(360 / (angle * (1 - preset["overlap"])))))
        views = min(16, views + views % 2)
        reasons["views"] = f"กระจายรอบ 360° ให้มุมข้างเคียงซ้อนกันประมาณ {round(preset['overlap']*100)}%"
    else:
        scale = min(1, preset["video_resolution"] / max(profile["width"], profile["height"]))
        width = max(160, round(profile["width"] * scale))
        height = max(160, round(profile["height"] * scale))
        reasons["width"] = reasons["height"] = "รักษาสัดส่วนเฟรมต้นฉบับและจำกัดขนาดตามคุณภาพ " + quality
        views = 1
        reasons["views"] = "วิดีโอปกติเก็บ 1 ภาพต่อเฟรม ไม่แตกมุม 360"
    fps = positive_number(profile.get("fps"))
    sample = recommended_sample_every(fps) if profile["kind"] == "video" else 1
    reasons["sample_every"] = (
        f"เริ่มที่ประมาณ 3 ตำแหน่งต่อวินาที: {fps:g} FPS เก็บทุก {sample} เฟรม (ปัดเป็นจำนวนเต็ม); แผนที่ทดสอบอาจแนะนำให้เก็บถี่ขึ้น"
        if fps and profile["kind"] == "video" else "ภาพนิ่งหรือไม่ทราบ FPS จึงใช้ทุก 1 เฟรม"
    )
    history_name = ("Model สำเร็จ" if selected and selected.get("level") == 2 else
                    "Camera Pose สำเร็จ" if proven else "Views สำเร็จ")
    history_reason = f"อ้างอิง {history_name} จาก job {selected['job']}" if selected else ""
    # View-only history proves image preparation, so reuse sizes only. Poses or
    # completed models provide evidence for FOV, overlap and temporal sampling.
    if selected:
        if projection == "panorama":
            old_side = min(int(old.get("width", width)), int(old.get("height", height)), width)
            if old_side >= 160:
                width = height = old_side
                reasons["width"] = reasons["height"] = history_reason + "; จำกัดขนาดตามไฟล์ต้นฉบับ"
        else:
            old_ratio = float(old.get("width", width)) / max(1, float(old.get("height", height)))
            if abs(old_ratio - profile["aspect_ratio"]) < .02:
                old_scale = min(1, float(old.get("width", width)) / width,
                                float(old.get("height", height)) / height)
                width, height = max(160, round(width * old_scale)), max(160, round(height * old_scale))
                reasons["width"] = reasons["height"] = history_reason + "; รักษาสัดส่วนภาพ"
    if proven:
        if fov is None:
            reasons["fov"] = history_reason
        if projection == "panorama":
            minimum = max(4, math.ceil(360 / (angle * .7)))
            old_views = int(old.get("views", views))
            if minimum <= old_views <= 16:
                views = old_views
                reasons["views"] = history_reason + "; ตรวจว่ามุมยังซ้อนกันเพียงพอ"
    if fov is not None:
        reasons["fov"] = "ใช้ FOV ที่คุณเลือก"
    settings = {"width": width, "height": height, "fov": angle, "views": views,
                "sample_every": sample, "sampling_guard": "manual",
                "projection": projection, "quality": quality}
    estimate = view_estimate(profile, settings)
    warnings = []
    if profile["kind"] == "image":
        warnings.append("ภาพนิ่ง 360 แตกหลายมุมได้ แต่ยังเป็นกล้องตำแหน่งเดียว ต้องมีภาพจากหลายตำแหน่งเพื่อสร้างความลึก 3D")
    elif not fps:
        warnings.append("อ่าน FPS ไม่ได้ จึงแนะนำทุกเฟรมและยังคำนวณช่วงเวลาไม่ได้")
    if estimate["total_views"] and estimate["total_views"] > 8000:
        warnings.append("คลิปนี้จะสร้างภาพจำนวนมาก ควรแบ่งคลิปตามพื้นที่หรือช่วงถ่ายก่อนสร้าง model")
    return {
        "profile": profile, "settings": settings, "reasons": reasons, "estimate": estimate,
        "warnings": warnings,
        "history": {"eligible_runs": len(records), "selected": (
            {key: selected.get(key) for key in ("job", "run_id", "level", "coverage", "psnr")}
            if selected else None)},
    }
