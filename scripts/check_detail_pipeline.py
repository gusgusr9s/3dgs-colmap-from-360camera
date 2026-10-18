"""Exercise original-resolution PNG and floor masks using only an existing smoke job."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.web import server as app


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = app.job_manifest(args.job)
    if not manifest.get('label', '').startswith('GPU pipeline smoke'):
        raise SystemExit('Only a job created by smoke_gpu_pipeline.py may be used')
    if any(app.task_is_active(path.name) for path in app.JOBS.iterdir() if path.is_dir()):
        raise SystemExit('A job is active; wait before testing')
    preview = app.cached_pose_preview(app.JOBS / args.job)
    if len(preview.get('cameras', [])) < 6:
        raise SystemExit('Smoke job requires at least six registered cameras')
    original_models = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in app.model_files_for_job(args.job)}
    selection = [camera['frame_index'] for camera in preview['cameras'][:6]]
    app.QUALITY_PROFILES['preview'] = {**app.QUALITY_PROFILES['preview'], 'steps': 2001, 'max_splats': 50000}
    if not app.claim_task(args.job):
        raise SystemExit('Cannot claim smoke job')
    try:
        identifier = app.allocate_model_run(args.job, selection, preview['input_signature'])
        record = app.JOBS / args.job / 'model_runs' / identifier / 'status.json'
        app.write_json(record, {**app.read_json(record), 'training_options':
            {'detail': 'source_2048', 'floor': 'more_floor', 'filter_blur': True}})
        print('Detail smoke run: ' + identifier, flush=True)
        app.generate_3d(args.job, identifier=identifier, selected_frames=selection, selection_signature=preview['input_signature'])
    finally:
        app.release_task(args.job)
    status = app.read_json(record)
    if status.get('state') == 'done':
        from app.models.model_variants import build_fast_variant
        for item in app.compression_records():
            if item.get('parent_model') == status['model'] and item.get('state') in {'queued', 'running'}:
                build_fast_variant(app.MODELS / status['model'], item['id'])
    unchanged = all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in original_models.items())
    result = {'job': args.job, 'model': status, 'previous_models_unchanged': unchanged}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'state': status.get('state'), 'model': status.get('model'), 'error': status.get('error'),
                      'quality': status.get('quality'), 'previous_models_unchanged': unchanged}, ensure_ascii=False), flush=True)
    return 0 if status.get('state') == 'done' and unchanged else 1


if __name__ == '__main__':
    raise SystemExit(main())
