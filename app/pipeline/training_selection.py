"""Immutable subsets of registered cameras and their supported sparse seeds."""

import json
import os
from pathlib import Path
import re
import shutil

import cv2
import numpy as np
from plyfile import PlyData, PlyElement


def validate_selection(preview, selected_frames, signature=None):
    if not isinstance(selected_frames, list) or any(type(item) is not int for item in selected_frames):
        raise ValueError("ตำแหน่งที่เลือกต้องเป็นรายการเลขเฟรม")
    if not preview or signature != preview.get("input_signature"):
        raise ValueError("ชุดภาพเปลี่ยนแล้วหรือยังไม่มี Camera Pose กรุณาเปิด Camera Preview ใหม่")
    selected = sorted(set(selected_frames))
    groups = {item["frame_index"]: item for item in preview["cameras"]}
    if len(selected) < 3:
        raise ValueError("เลือกอย่างน้อย 3 ตำแหน่งกล้องที่มีภาพซ้อนกัน แนะนำเริ่ม 10 ตำแหน่งต่อเนื่อง")
    if any(item not in groups for item in selected):
        raise ValueError("มีตำแหน่งที่ไม่ได้ลงทะเบียนใน Camera Preview")
    centers = np.asarray([groups[item]["position"] for item in selected])
    if np.linalg.norm(np.ptp(centers, axis=0)) < 1e-6:
        raise ValueError("กล้องที่เลือกอยู่ตำแหน่งเดียวกัน จึงไม่มีความลึกให้ train")
    return selected


def link_or_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def supported_colmap_points(job_dir, selected, centers):
    """Native spherical COLMAP tracks share the dataset's world coordinates."""
    import pycolmap
    from app.pipeline.colmap_recovery import saved_models
    metadata=json.loads((job_dir/'brush_data/transforms.json').read_text(encoding='utf8')) if (job_dir/'brush_data/transforms.json').is_file() else {}
    hint=(job_dir/metadata['colmap_model_path']).resolve() if metadata.get('colmap_model_path') else None
    models=([hint] if hint and hint.is_relative_to(job_dir.resolve()) and (hint/'points3D.bin').is_file() else [])
    models += [path for _,path in saved_models(job_dir) if path not in models]
    for model in models:
        reconstruction = pycolmap.Reconstruction(str(model))
        image_ids = set()
        matches = True
        for image in reconstruction.images.values():
            match = re.fullmatch(r"pano_(\d+)", Path(image.name).stem)
            index = int(match[1]) if match else None
            if index in selected:
                actual = image.cam_from_world().inverse().matrix()[:, 3]
                if not np.allclose(actual, centers[index], atol=1e-5, rtol=1e-6):
                    matches = False
                    break
                image_ids.add(image.image_id)
        if not matches or len(image_ids) != len(selected):
            continue
        points = []
        for point in reconstruction.points3D.values():
            observed = {element.image_id for element in point.track.elements if element.image_id in image_ids}
            if len(observed) >= 2 and np.isfinite(point.error) and point.error <= 2.5:
                points.append((*point.xyz, *point.color))
        return np.array(points, dtype=[(key, "<f4") for key in ("x", "y", "z")]
                        + [(key, "u1") for key in ("red", "green", "blue")])
    return None


def build_training_subset(job_dir, destination, selected_frames, preview):
    selected = validate_selection(preview, selected_frames, preview["input_signature"])
    source_dir = job_dir / "brush_data"
    dataset = json.loads((source_dir / "transforms.json").read_text(encoding="utf-8"))
    frames = [frame for frame in dataset["frames"] if frame.get("frame_index") in selected]
    if not frames:
        raise ValueError("ไม่มีภาพในตำแหน่งที่เลือก")
    destination.mkdir(parents=True, exist_ok=False)
    for frame in frames:
        relative = Path(frame["file_path"])
        image = (source_dir / relative).resolve()
        target = (destination / relative).resolve()
        if source_dir.resolve() not in image.parents or destination.resolve() not in target.parents or not image.is_file():
            raise ValueError("พาธภาพในชุด train ไม่ถูกต้อง")
        link_or_copy(image, target)
        mask = source_dir / "masks" / (relative.stem + ".png")
        if mask.is_file():
            link_or_copy(mask, destination / "masks" / mask.name)
    centers = {item["frame_index"]: np.asarray(item["position"]) for item in preview["cameras"]}
    spherical = json.loads((job_dir / "transforms.json").read_text(encoding="utf-8")).get("source_mode") == "panorama"
    vertices = supported_colmap_points(job_dir, selected, centers) if spherical else None
    seed_filter = "COLMAP tracks in at least two selected positions" if vertices is not None else "visibility in at least two selected positions"
    original = PlyData.read(source_dir / dataset.get("ply_file_path", "sparse_pc.ply"))["vertex"].data
    if vertices is None:
        vertices = original.copy()
    xyz = np.column_stack([vertices[key] for key in ("x", "y", "z")]).astype(np.float64)
    valid = np.isfinite(xyz).all(axis=1)
    counts = np.zeros(len(vertices), dtype=np.int32)
    # Count positions, not multiple directions at the same camera center.
    for index in selected:
        supported = np.zeros(len(vertices), dtype=bool)
        for frame in (item for item in frames if item["frame_index"] == index):
            matrix = np.asarray(frame["transform_matrix"], dtype=np.float64)
            local = (xyz - matrix[:3, 3]) @ matrix[:3, :3]
            depth = -local[:, 2]
            width, height = int(frame.get("w", dataset["w"])), int(frame.get("h", dataset["h"]))
            fx, fy = float(frame.get("fl_x", dataset["fl_x"])), float(frame.get("fl_y", dataset["fl_y"]))
            cx, cy = float(frame.get("cx", dataset.get("cx", width/2))), float(frame.get("cy", dataset.get("cy", height/2)))
            safe_depth = np.maximum(depth, 1e-8)
            px, py = fx * local[:, 0] / safe_depth + cx, -fy * local[:, 1] / safe_depth + cy
            inside = valid & (depth > 1e-6) & (px >= 0) & (px < width) & (py >= 0) & (py < height)
            mask_path = source_dir / "masks" / (Path(frame["file_path"]).stem + ".png")
            mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE) if mask_path.is_file() else None
            ids = np.flatnonzero(inside)
            if mask is not None and len(ids):
                mx = np.minimum(mask.shape[1]-1, (px[ids] * mask.shape[1] / width).astype(int))
                my = np.minimum(mask.shape[0]-1, (py[ids] * mask.shape[0] / height).astype(int))
                inside[ids] &= mask[my, mx] > 127
            supported |= inside
        counts += supported
    vertices = vertices[valid & (counts >= 2)]
    if len(vertices) < 100:
        raise ValueError(f"ชุดที่เลือกเหลือจุดตั้งต้นที่มีภาพรองรับเพียง {len(vertices)} จุด ลองเพิ่มตำแหน่งที่เห็นบริเวณเดียวกัน")
    PlyData([PlyElement.describe(vertices, "vertex")], text=True).write(destination / "sparse_pc.ply")
    dataset.update(frames=frames, ply_file_path="sparse_pc.ply")
    (destination / "transforms.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    selected_indices = [i for i, camera in enumerate(preview["cameras"]) if camera["frame_index"] in selected]
    selected_set = set(selected_indices)
    warnings = []
    jumps = sum(segment["suspicious"] for segment in preview["segments"]
                if segment["from"] in selected_set and segment["to"] in selected_set)
    if jumps:
        warnings.append(f"ชุดที่เลือกมี {jumps} ช่วงที่ pose กระโดด ควรตรวจภาพก่อน train")
    if any(b-a > 1 for a,b in zip(selected_indices, selected_indices[1:])):
        warnings.append("ตำแหน่งที่เลือกไม่ต่อเนื่อง ควรตรวจว่าภาพยังเห็นฉากส่วนเดียวกัน")
    return {
        "frame_indices": selected, "positions": len(selected), "views": len(frames),
        "total_positions": len(preview["cameras"]), "original_sparse_points": len(original),
        "sparse_points": len(vertices), "seed_filter": seed_filter, "warnings": warnings,
        "input_signature": preview["input_signature"],
    }
