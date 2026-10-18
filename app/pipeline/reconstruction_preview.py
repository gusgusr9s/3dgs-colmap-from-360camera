"""Build a camera and sparse-point preview from the dataset used for training."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import numpy as np
from plyfile import PlyData


def pose_input_signature(view_dir: Path) -> str:
    metadata = json.loads((view_dir / "transforms.json").read_text(encoding="utf-8"))
    inputs = {key: metadata.get(key) for key in (
        "source_mode", "w", "h", "fl_x", "fl_y", "cx", "cy", "frames",
    )}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def build_pose_preview(view_dir: Path, quality: dict | None = None) -> dict:
    source = json.loads((view_dir / "transforms.json").read_text(encoding="utf-8"))
    dataset_dir = view_dir / "brush_data"
    dataset = json.loads((dataset_dir / "transforms.json").read_text(encoding="utf-8"))
    for key in ("w", "h", "fl_x", "fl_y", "cx", "cy"):
        if key not in dataset and key in source:
            dataset[key] = source[key]
    spherical = source.get("source_mode") == "panorama"
    groups = {}
    views = []
    job_url = "/jobs/" + quote(view_dir.name, safe="")
    for ordinal, frame in enumerate(dataset.get("frames", [])):
        matrix = np.asarray(frame["transform_matrix"], dtype=np.float64)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError("Camera preview contains an invalid camera matrix")
        relative = Path(frame["file_path"])
        image_path = (dataset_dir / relative).resolve()
        if dataset_dir.resolve() not in image_path.parents or not image_path.is_file():
            raise ValueError(f"Camera preview image is missing: {relative.name}")
        frame_index = int(frame.get("frame_index", ordinal))
        group_key = frame_index if spherical else ordinal
        if group_key not in groups:
            groups[group_key] = {
                "frame_index": frame_index,
                "position": matrix[:3, 3].tolist(),
                "views": [],
            }
        elif np.linalg.norm(matrix[:3, 3] - groups[group_key]["position"]) > 1e-7:
            raise ValueError("Views from one panorama do not share the same camera position")
        groups[group_key]["views"].append(len(views))
        views.append({
            "name": relative.name,
            "image_url": job_url + "/brush_data/" + quote(relative.as_posix(), safe="/"),
            "frame_index": frame_index,
            "yaw_deg": frame.get("yaw_deg"),
            "pitch_deg": frame.get("pitch_deg"),
            "transform_matrix": matrix.tolist(),
            "w": int(frame.get("w", dataset["w"])),
            "h": int(frame.get("h", dataset["h"])),
            "fl_x": float(frame.get("fl_x", dataset["fl_x"])),
            "fl_y": float(frame.get("fl_y", dataset["fl_y"])),
            "cx": float(frame.get("cx", dataset.get("cx", dataset["w"] / 2))),
            "cy": float(frame.get("cy", dataset.get("cy", dataset["h"] / 2))),
        })
    cameras = [groups[key] for key in sorted(groups)]
    if not cameras:
        raise ValueError("No registered cameras are available for preview")

    vertices = PlyData.read(dataset_dir / dataset.get("ply_file_path", "sparse_pc.ply"))["vertex"].data
    xyz = np.column_stack([vertices[key] for key in ("x", "y", "z")])
    finite = np.isfinite(xyz).all(axis=1)
    xyz = xyz[finite]
    stride = max(1, int(np.ceil(len(xyz) / 30000)))
    points = {"positions": xyz[::stride].astype(np.float32).ravel().tolist()}
    if all(key in vertices.dtype.names for key in ("red", "green", "blue")):
        rgb = np.column_stack([vertices[key][finite] for key in ("red", "green", "blue")])
        points["colors"] = (rgb[::stride].astype(np.float32) / 255).ravel().tolist()

    warnings = []
    missing = []
    input_positions = len(source.get("frames", []))
    if spherical:
        original = {int(frame["frame_index"]) for frame in source["frames"]}
        missing = sorted(original - set(groups))
        input_positions = len(original)
        if missing:
            warnings.append(f"COLMAP ยังหาตำแหน่งไม่ได้ {len(missing)}/{len(original)} เฟรม 360")
    elif len(views) < input_positions:
        warnings.append(f"COLMAP ลงทะเบียนได้ {len(views)}/{input_positions} ภาพ")

    centers = np.asarray([camera["position"] for camera in cameras])
    distances = np.linalg.norm(np.diff(centers, axis=0), axis=1)
    intervals = np.maximum(1, np.diff([camera["frame_index"] for camera in cameras]))
    velocities = distances / intervals
    positive = velocities[velocities > max(float(np.ptp(centers, axis=0).max()), 1e-6) * 1e-5]
    median_step = float(np.median(positive)) if len(positive) else 0.0
    segments = []
    for index, distance in enumerate(distances):
        ratio = float(velocities[index] / median_step) if median_step else 0.0
        segments.append({"from": index, "to": index + 1,
                         "distance": float(distance), "suspicious": ratio > 6.0})
    jumps = sum(segment["suspicious"] for segment in segments)
    if jumps:
        warnings.append(f"พบ {jumps} ช่วงที่ตำแหน่งกล้องกระโดดมากกว่าช่วงอื่น · ตรวจคู่ภาพบนเส้นสีแดง")
    if not len(positive):
        warnings.append("กล้องแทบไม่เคลื่อนตำแหน่ง · การหมุนอย่างเดียวไม่ให้ความลึกสำหรับสร้าง 3D")

    payload = {
        "version": 1,
        "input_signature": pose_input_signature(view_dir),
        "coordinate_system": "camera-to-world, +Y up, -Z forward; relative scale",
        "cameras": cameras, "views": views, "segments": segments,
        "points": points, "unregistered_frames": missing,
        "summary": {"registered_positions": len(cameras), "input_positions": input_positions,
                    "registered_views": len(views), "input_views": len(source.get("frames", [])),
                    "sparse_points": int(len(vertices)), "preview_points": len(points["positions"]) // 3,
                    "suspicious_segments": jumps},
        "warnings": warnings, "quality": quality or {},
        "capture_plan": source.get("capture_plan", {}), "settings": source.get("settings", {}),
    }
    destination = view_dir / "pose_preview.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(destination)
    return payload
