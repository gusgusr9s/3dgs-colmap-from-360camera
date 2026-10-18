"""Build a perspective Brush dataset from a spherical COLMAP trajectory."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import pycolmap
from app.pipeline import dataset_preparation

from app.pipeline.pano_to_views import perspective_map
from app.runtime.compute_runtime import select_mask_device


def yaw_rotation(yaw_deg: float) -> np.ndarray:
    yaw = math.radians(yaw_deg)
    return np.array(
        [
            [math.cos(yaw), 0.0, math.sin(yaw)],
            [0.0, 1.0, 0.0],
            [-math.sin(yaw), 0.0, math.cos(yaw)],
        ],
        dtype=np.float64,
    )


def crop_rotation(yaw_deg: float, pitch_deg: float) -> np.ndarray:
    """Match pano_to_views rotation in COLMAP's +Y-down camera coordinates."""
    pitch = math.radians(-pitch_deg)
    pitch_rotation = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, math.cos(pitch), -math.sin(pitch)],
            [0.0, math.sin(pitch), math.cos(pitch)],
        ],
        dtype=np.float64,
    )
    return yaw_rotation(yaw_deg) @ pitch_rotation


def write_sparse_ply(reconstruction: pycolmap.Reconstruction, output: Path) -> int:
    """Write COLMAP's sparse points in the same world coordinates as the poses."""
    points = list(reconstruction.points3D.values())
    with output.open("w", encoding="ascii", newline="\n") as handle:
        handle.write("ply\nformat ascii 1.0\n")
        handle.write(f"element vertex {len(points)}\n")
        handle.write("property float x\nproperty float y\nproperty float z\n")
        handle.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
        handle.write("end_header\n")
        for point in points:
            x, y, z = point.xyz
            red, green, blue = point.color
            handle.write(f"{x:.8g} {y:.8g} {z:.8g} {red} {green} {blue}\n")
    return len(points)


def create_dynamic_person_masks(dataset_dir: Path, progress_callback=None, _gpu_worker=False) -> dict:
    """Create Brush ignore masks for people, including the nearby camera operator."""
    from app.runtime.mask_compute import MaskPredictor, worker_masks
    if not _gpu_worker:
        result = worker_masks(dataset_dir, progress_callback)
        if result is not None:
            return result
    try:
        import torch
        from PIL import Image
        from torchvision.models.segmentation import (
            LRASPP_MobileNet_V3_Large_Weights,
            lraspp_mobilenet_v3_large,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Automatic person masking requires torch and torchvision. Run app_1_install.ps1 (Windows) or bash app_1_install.sh (Ubuntu)."
        ) from exc

    image_paths = sorted((dataset_dir / "images").glob("*"))
    image_paths = [path for path in image_paths if path.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    if not image_paths:
        return {"masked_images": 0, "mean_ignored_pixels_percent": 0.0}
    metadata = json.loads((dataset_dir / "transforms.json").read_text(encoding="utf-8"))
    if metadata.get('frames'):
        image_paths = sorted({(dataset_dir / frame['file_path']).resolve() for frame in metadata['frames']})
        if any(not path.is_relative_to(dataset_dir.resolve()) for path in image_paths):
            raise ValueError('พาธภาพ Mask ไม่ถูกต้อง')
    floor_cutoff = float(metadata.get('mask_floor_cutoff', .72))
    if floor_cutoff not in {.72, .9, 1.0}:
        raise ValueError('ตัวเลือกพื้นที่พื้นไม่ถูกต้อง')
    frames_by_stem = {
        Path(frame.get("file_path", "")).stem: frame for frame in metadata.get("frames", [])
    }
    focal_x = float(metadata.get("fl_x", 0.0))
    image_width = int(metadata.get("w", 0))
    fov_deg = math.degrees(2 * math.atan(image_width / (2 * focal_x))) if focal_x > 0 else 90.0
    mask_dir = dataset_dir / "masks"

    weights = LRASPP_MobileNet_V3_Large_Weights.DEFAULT
    try:
        model = lraspp_mobilenet_v3_large(weights=weights).eval()
    except Exception as exc:
        raise RuntimeError(
            "Could not load the 13 MB PyTorch person-mask model. Check the internet connection "
            "for the first run, then try Generate 3D again."
        ) from exc
    predictor = MaskPredictor(model)
    if _gpu_worker:
        from app.runtime.cpu_runtime import cpu_budget
        predictor.cpu_runtime=cpu_budget().metadata()
    if progress_callback:
        progress_callback(done=0, total=len(image_paths), compute=predictor.metadata)
    # Validate GPU and finish all masks before replacing an existing usable set.
    staged_masks = Path(tempfile.mkdtemp(prefix="masks-", suffix=".partial", dir=dataset_dir))
    try:
        transform = weights.transforms()
        ignored_fractions = []
        nadir_fractions = []
        masked_images = 0
        nadir_masks: dict[tuple[int, int, float], np.ndarray] = {}
        batch_size = 4
        for start in range(0, len(image_paths), batch_size):
            paths = image_paths[start:start + batch_size]
            originals = [Image.open(path).convert("RGB") for path in paths]
            inputs = torch.stack([transform(image) for image in originals])
            person_probabilities = predictor.probabilities(inputs)

            for path, original, probability in zip(paths, originals, person_probabilities):
                width, height = original.size
                probability = cv2.resize(probability, (width, height), interpolation=cv2.INTER_LINEAR)
                ignored = (probability > 0.05).astype(np.uint8)

                frame = frames_by_stem.get(path.stem, {})
                pitch_deg = float(frame.get("pitch_deg", 0.0))
                nadir_key = (width, height, pitch_deg)
                nadir = nadir_masks.get(nadir_key)
                if nadir is None:
                    _, latitude_map_y, _ = perspective_map(
                        width, height, fov_deg, 0.0, pitch_deg
                    )
                    # Ignore latitudes below roughly -40 degrees.  This removes
                    # the capture car/tripod/selfie stick while retaining most
                    # nearby ground for reconstruction.
                    nadir = (latitude_map_y > (height - 1) * floor_cutoff).astype(np.uint8)
                    nadir_masks[nadir_key] = nadir
                ignored = np.maximum(ignored, nadir)
                nadir_fractions.append(float(np.mean(nadir)))
                ignored = cv2.morphologyEx(
                    ignored, cv2.MORPH_CLOSE, np.ones((21, 21), dtype=np.uint8)
                )
                ignored = cv2.dilate(ignored, np.ones((15, 15), dtype=np.uint8))

                # A close operator or selfie stick is often only partially recognized.
                # Fill the hull of every large component that reaches the lower image.
                contours, _ = cv2.findContours(ignored, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for contour in contours:
                    x, y, box_width, box_height = cv2.boundingRect(contour)
                    if cv2.contourArea(contour) > width * height * 0.01 and y + box_height > height * 0.65:
                        cv2.fillConvexPoly(ignored, cv2.convexHull(contour), 1)
                        cv2.rectangle(
                            ignored,
                            (max(0, x - 12), y),
                            (min(width - 1, x + box_width + 12), height - 1),
                            1,
                        )

                fraction = float(np.mean(ignored))
                ignored_fractions.append(fraction)
                if fraction > 0.001:
                    masked_images += 1
                # Brush mask convention: white pixels train, black pixels are ignored.
                valid_mask = np.where(ignored > 0, 0, 255).astype(np.uint8)
                if not cv2.imwrite(str(staged_masks / f"{path.stem}.png"), valid_mask):
                    raise RuntimeError(f"Could not write dynamic mask for {path.name}")
            if progress_callback:
                progress_callback(done=min(start + batch_size, len(image_paths)), total=len(image_paths), compute=predictor.metadata)
            for original in originals:
                original.close()

        previous = staged_masks.with_name(staged_masks.name + ".previous")
        if mask_dir.exists():
            mask_dir.rename(previous)
        try:
            staged_masks.rename(mask_dir)
        except OSError:
            if previous.exists():
                previous.rename(mask_dir)
            raise
        if previous.exists():
            shutil.rmtree(previous)

        return {
            "masked_images": masked_images,
            "mean_ignored_pixels_percent": round(float(np.mean(ignored_fractions)) * 100, 3),
            "mean_nadir_mask_percent": round(float(np.mean(nadir_fractions)) * 100, 3),
            "mask_model": "LRASPP MobileNetV3 person segmentation",
            "mask_device": "directml" if predictor.backend == "DirectML" else str(predictor.device),
            "mask_compute_device": predictor.device_name,
            "mask_compute": predictor.metadata,
        }
    finally:
        assert staged_masks.resolve().parent == dataset_dir.resolve()
        shutil.rmtree(staged_masks, ignore_errors=True)


def build_brush_dataset(view_dir: Path, model_dir: Path, output_dir: Path, progress_callback=None) -> dict:
    """Publish a complete dataset atomically; cache encoded pixels across stops."""
    view_dir=Path(view_dir).resolve();output_dir=Path(output_dir).resolve()
    output_dir.parent.mkdir(parents=True,exist_ok=True)
    staged=Path(tempfile.mkdtemp(prefix=output_dir.name+'.building-',dir=output_dir.parent))
    previous=staged.with_name(staged.name+'.previous')
    try:
        result=_build_brush_dataset(view_dir,model_dir,staged,progress_callback,view_dir/'.dataset_cache/jpeg95')
        if output_dir.exists():output_dir.rename(previous)
        try:staged.rename(output_dir)
        except OSError:
            if previous.exists():previous.rename(output_dir)
            raise
        if previous.exists():
            assert previous.resolve().parent==output_dir.parent
            shutil.rmtree(previous)
        return result
    finally:
        if staged.exists():
            assert staged.resolve().parent==output_dir.parent
            shutil.rmtree(staged)


def _build_brush_dataset(view_dir,model_dir,output_dir,progress_callback,cache_dir):
    """Give every crop from one panorama an identical center and a known yaw."""
    view_dir = view_dir.resolve()
    output_dir = output_dir.resolve()
    metadata = json.loads((view_dir / "transforms.json").read_text(encoding="utf-8"))
    reconstruction = pycolmap.Reconstruction(str(model_dir.resolve()))

    panorama_poses: dict[int, np.ndarray] = {}
    for image in reconstruction.images.values():
        name = Path(image.name).stem
        if not name.startswith("pano_"):
            continue
        frame_index = int(name.removeprefix("pano_"))
        pose = np.eye(4, dtype=np.float64)
        pose[:3, :] = image.cam_from_world().inverse().matrix()
        panorama_poses[frame_index] = pose

    if len(panorama_poses) < 3:
        raise RuntimeError("Spherical COLMAP registered fewer than 3 panorama frames")

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True)
    frames = []
    image_pairs=[]
    centers_by_frame: dict[int, list[np.ndarray]] = {}
    for frame in metadata["frames"]:
        frame_index = int(frame["frame_index"])
        panorama_pose = panorama_poses.get(frame_index)
        if panorama_pose is None:
            continue

        # Compose the known crop yaw/pitch with the single spherical pose.
        # The pitch sign changes because the crop renderer uses +Y up while
        # COLMAP camera coordinates use +Y down.
        c2w = panorama_pose.copy()
        pitch_deg = float(frame.get("pitch_deg", 0.0))
        c2w[:3, :3] = panorama_pose[:3, :3] @ crop_rotation(
            float(frame["yaw_deg"]), pitch_deg
        )
        # COLMAP cameras use +Y down and +Z forward. Nerfstudio/Brush camera
        # matrices use +Y up and -Z forward.
        c2w[:3, 1:3] *= -1.0

        source = (view_dir / frame["file_path"]).resolve()
        if source.parent != view_dir or not source.is_file():
            raise RuntimeError(f"Perspective image is missing: {frame['file_path']}")
        destination = image_dir / source.name
        image_pairs.append((source,destination))

        frames.append(
            {
                "file_path": f"images/{destination.name}",
                "transform_matrix": c2w.tolist(),
                "frame_index": frame_index,
                "yaw_deg": float(frame["yaw_deg"]),
                "pitch_deg": pitch_deg,
            }
        )
        centers_by_frame.setdefault(frame_index, []).append(c2w[:3, 3])

    image_records,image_timing=dataset_preparation.prepare_images(image_pairs,cache_dir,
        metadata['w'],metadata['h'],progress_callback)

    output = {
        "camera_model": "OPENCV",
        "fl_x": metadata["fl_x"],
        "fl_y": metadata["fl_y"],
        "cx": metadata["cx"],
        "cy": metadata["cy"],
        "w": metadata["w"],
        "h": metadata["h"],
        "k1": 0.0,
        "k2": 0.0,
        "p1": 0.0,
        "p2": 0.0,
        "frames": frames,
        "ply_file_path": "sparse_pc.ply",
        "colmap_model_path": str(model_dir.resolve().relative_to(view_dir)) if model_dir.resolve().is_relative_to(view_dir) else None,
    }
    (output_dir / "transforms.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    point_count = write_sparse_ply(reconstruction, output_dir / "sparse_pc.ply")
    from app.pipeline.reconstruction_preview import pose_input_signature
    import hashlib
    from app.jobs.task_control import write
    write(output_dir/'dataset_complete.json',{'schema':1,'input_signature':pose_input_signature(view_dir),
          'transforms_sha256':hashlib.sha256((output_dir/'transforms.json').read_bytes()).hexdigest(),
          'images':image_records,'seed_stamp':dataset_preparation.stamp(output_dir/'sparse_pc.ply')})
    max_center_spread = max(
        np.ptp(np.asarray(centers), axis=0).max() for centers in centers_by_frame.values()
    )
    camera_centers = np.asarray([pose[:3, 3] for pose in panorama_poses.values()])
    camera_path_span = float(np.linalg.norm(np.ptp(camera_centers, axis=0)))
    sparse_positions = np.asarray([point.xyz for point in reconstruction.points3D.values()])
    sparse_center = np.median(sparse_positions, axis=0)
    scene_radius = float(np.percentile(np.linalg.norm(sparse_positions - sparse_center, axis=1), 90))
    return {
        "registered_panoramas": len(panorama_poses),
        "perspective_views": len(frames),
        "sparse_points": point_count,
        "max_same_frame_center_spread": float(max_center_spread),
        "camera_path_span": camera_path_span,
        "scene_radius_p90": scene_radius,
        "path_to_scene_ratio": camera_path_span / max(scene_radius, 1e-9),
        "mean_reprojection_error": float(reconstruction.compute_mean_reprojection_error()),
        "dataset_preparation":image_timing,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--views", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build_brush_dataset(args.views, args.model, args.output), indent=2))


if __name__ == "__main__":
    main()
