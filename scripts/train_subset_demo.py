"""Train a real, 10-position demo while preserving the original job and model."""

from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import server as app
from app.pipeline.training_selection import link_or_copy


parent = "50bb86a013"
job = "temple_subset10_" + app.run_id()[-8:]
source_dir = app.JOBS / parent
directory = app.JOBS / job
assert directory.resolve().parent == app.JOBS.resolve()
directory.mkdir()
for image in source_dir.glob("frame_*.jpg"):
    link_or_copy(image, directory / image.name)
link_or_copy(source_dir / "transforms.json", directory / "transforms.json")
for source in (source_dir / "brush_data").rglob("*"):
    if source.is_file():
        link_or_copy(source, directory / "brush_data" / source.relative_to(source_dir / "brush_data"))
# Track coordinates and observations are read from the original spherical model.
for source in (source_dir / "spherical_colmap" / "sparse").rglob("*"):
    if source.is_file():
        link_or_copy(source, directory / "spherical_colmap" / "sparse" / source.relative_to(source_dir / "spherical_colmap" / "sparse"))
info = app.job_manifest(parent)
settings = {**info["settings"], "quality": "preview"}
app.save_job_manifest(job, {"label": "Temple · 10 consecutive positions · GPU comparison",
    "source_name": info["source_name"], "source_key": info["source_key"], "settings": settings,
    "parent_job": parent, "created_at": app.utc_now(),
    "source_profile": app.probe_source(app.source_from_key(info["source_key"]))})
app.write_json(directory / "views_status.json", app.views_status(parent))
app.write_json(directory / "pose_status.json", {"state": "ready", "stage": "poses", "progress": 100})
preview = app.build_pose_preview(directory, (app.model_status(parent) or {}).get("quality", {}))
selected = [camera["frame_index"] for camera in preview["cameras"][:10]]
print(json.dumps({"job": job, "selected_frames": selected, "steps": 10000}), flush=True)
assert app.claim_task(job)
app.generate_3d(job, selected_frames=selected, selection_signature=preview["input_signature"])
status = app.model_status(job)
print(json.dumps(status, ensure_ascii=False), flush=True)
raise SystemExit(0 if status["state"] == "done" else 1)
