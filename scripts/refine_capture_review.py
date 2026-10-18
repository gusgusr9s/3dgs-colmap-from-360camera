"""Wait for the baseline GPU experiments, then verify automatic seed filtering."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.web import server as app
from app.pipeline.training_selection import link_or_copy

job='a147ed6bb2'
baseline=app.model_status(job)
assert baseline.get('state')=='done'
while app.task_is_active('bb41eed643'):
    time.sleep(5)
assert app.claim_task(job)
try:
    print('Testing automatic track/mask seed filtering with all 13 registered positions',flush=True)
    app.generate_3d(job)
finally:
    app.release_task(job)
status=app.model_status(job)
if status.get('state')=='done':
    assert status.get('selection',{}).get('mode')=='all_registered_with_seed_filter', 'Automatic seed filter was not applied'
run_dir=app.JOBS/job/'model_runs'/status['run_id']
old_dir=app.JOBS/job/'model_runs'/baseline['run_id']/'brush_export'/f"eval_{baseline['quality']['training_steps']}"
for source in old_dir.glob('*.png'):
    link_or_copy(source,run_dir/'baseline'/source.name)
review={'job':job,'baseline':baseline,'filtered':status,
        'limits':['Same frames and 20,000 steps, but separate stochastic training runs.',
                  'PSNR is calculated only outside masks; masked people can still ghost.',
                  'Held-out evaluation renders are before published PLY filtering.']}
app.write_json(app.ROOT/'web_data'/'capture_review'/'seed_filter_results.json',review)
print(json.dumps({'job':job,'state':status.get('state'),'model':status.get('model'),
                  'quality':status.get('quality'),'selection':status.get('selection'),
                  'error':status.get('error')},ensure_ascii=True),flush=True)
raise SystemExit(0 if status.get('state')=='done' else 1)
