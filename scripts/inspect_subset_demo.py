"""Record same-camera comparisons using actual GPU renders and valid masks."""

import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import server as app
from app.pipeline.training_selection import link_or_copy

job = sys.argv[1] if len(sys.argv) > 1 else "temple_subset10_e3509737"
directory = app.job_directory(job)
if directory is None:
    raise SystemExit("Job not found")
status = app.model_status(job)
identifier = status["run_id"]
run_dir = directory / "model_runs" / identifier
page = app.model_quality_page(job, identifier)
images = json.loads(re.search(r'id="config">(.*?)</script>', page, re.S)[1])
for item in images:
    if item["previous"]:
        relative = Path(item["previous"].removeprefix("/jobs/"))
        source = app.JOBS / relative
        destination = run_dir / "baseline" / source.name
        if not destination.exists():
            link_or_copy(source, destination)
        item["previous"] = "/jobs/" + destination.relative_to(app.JOBS).as_posix()
report = {
    "job": job, "run_id": identifier, "model": status["model"],
    "gpu": status["quality"].get("compute_device"), "steps": status["quality"]["training_steps"],
    "elapsed_seconds": status["elapsed_seconds"], "selection": status["selection"],
    "images": images, "same_view_comparisons": [item for item in images if item["previous"]],
    "limitations": [
        "These are actual Brush GPU renders; browser screenshots were unavailable.",
        "Only one held-out view overlaps the old full-model eval split.",
        "Full-model and subset training used different steps and image counts.",
        "PSNR excludes masked pixels; it does not prove complete or stable geometry.",
        "GPU evaluation images precede outlier filtering of the published PLY.",
        "Moving people and distant background still show blur and missing detail.",
    ],
}
app.write_json(run_dir / "quality_review.json", report)
print(json.dumps({"review": str(run_dir / "quality_review.json"), "comparisons": [
    {key: item[key] for key in ("name", "previous_psnr", "psnr")} for item in report["same_view_comparisons"]
]}, indent=2))
