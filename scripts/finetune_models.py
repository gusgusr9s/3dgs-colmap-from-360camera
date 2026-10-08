"""Run actual checkpoint fine tuning sequentially and save before/after measurements."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_app as app
from diagnose_models import diagnose

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', action='append', required=True)
    args = parser.parse_args()
    for model in args.model:
        info, _, _ = app.refinement_input(model)
        job = info['job']
        if any(app.task_is_active(d.name) for d in app.JOBS.iterdir() if d.is_dir()):
            raise RuntimeError('Wait for the current task before running this experiment')
        if not app.claim_task(job):
            raise RuntimeError('Job is busy')
        identifier = app.allocate_model_run(job)
        print(json.dumps({'parent': model, 'job': job, 'new_run': identifier}), flush=True)
        app.refine_model(model, identifier)
        result = app.read_json(app.JOBS / job / 'model_runs' / identifier / 'status.json')
        print(json.dumps({'state': result['state'], 'model': result.get('model'),
                          'quality': result.get('quality'), 'error': result.get('error')}, ensure_ascii=True), flush=True)
        if result['state'] == 'done':
            review = {'before': diagnose(job, info['run_id']), 'after': diagnose(job, identifier),
                      'parent_model': model, 'model': result['model']}
            app.write_json(app.JOBS / job / 'model_runs' / identifier / 'quality_review.json', review)
            print(json.dumps({'before': review['before']['render_metrics_mean'],
                              'after': review['after']['render_metrics_mean']}), flush=True)
