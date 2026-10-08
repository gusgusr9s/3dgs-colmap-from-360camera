"""Sequential real HTTP upload/generation path, resumable with --batch.

Runs COLMAP and Brush on the actual GPU. No supplied camera poses or models
are imported. Results (including failures) are visible at /experiments.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_app as app


@contextmanager
def batch_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = app.uuid.uuid4().hex
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump({'pid':os.getpid(),'token':token},handle)
            break
        except FileExistsError:
            if app.task_lock_alive(path):
                raise RuntimeError('This batch already has a running test process: '+path.stem)
            path.unlink(missing_ok=True)
    try:
        yield
    finally:
        if app.read_json(path, {}).get('token') == token:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8001')
    parser.add_argument('--batch', default='real360_20261008')
    parser.add_argument('--case', action='append')
    args = parser.parse_args()
    if not args.batch.replace('_', '').replace('-', '').isalnum():
        raise ValueError('Invalid batch name')
    output = app.ROOT/'web_data'/'experiments'/(args.batch+'.json')
    with batch_lock(output.with_suffix('.lock')):
        run_cases(args, output)


def run_cases(args, output):
    catalog = app.read_json(app.RAW/'test_catalog.json', {})['cases']
    if args.case and set(args.case)-{case['id'] for case in catalog}:
        raise ValueError('Unknown case in test_catalog.json')
    cases = [case for case in catalog if not args.case or case['id'] in args.case]
    result = app.read_json(output, {'batch':args.batch, 'created_at':app.utc_now(), 'cases':[]})
    records = {case['id']:case for case in result['cases']}
    def save():
        result['updated_at'] = app.utc_now()
        app.write_json(output, result)
    def get(path):
        with urlopen(args.base+path, timeout=60) as response:
            return json.load(response)
    for case in cases:
        if case['id'] not in records:
            record = {**case, 'state':'queued'}
            records[case['id']] = record
            result['cases'].append(record)
    save()
    for case in cases:
        record = records[case['id']]
        if record.get('state') in {'done','error'}:
            print('Already tested: '+case['id'], flush=True)
            continue
        source = app.RAW/case['source']
        if not source.is_file():
            record.update(state='error', error='Source file missing; run download_omniphotos.py')
            save()
            continue
        # Serialize this batch with other active app tasks to avoid GPU contention.
        waiting = False
        while any(app.task_is_active(path.name) for path in app.JOBS.iterdir()
                  if path.is_dir() and path.name != record.get('job')):
            if not waiting:
                print('Waiting for other active tasks before '+case['id'], flush=True)
                waiting = True
            time.sleep(5)
        if not record.get('job'):
            key = 'data/raw:'+case['source']
            suggested = get('/api/view-suggestions?'+urlencode({'source':key}))
            settings = {**suggested['settings'], 'start_seconds':case['start_seconds'], 'end_seconds':case['end_seconds']}
            plan = get('/api/capture-plan?'+urlencode({'source':key, **settings}))
            with urlopen(Request(args.base+'/actions', data=urlencode({
                'action':'generate', 'source':key, 'label':case['label']+' · '+args.batch,
                'auto_model':'1', **settings}).encode()), timeout=60) as response:
                job = urlparse(response.url).path.split('/')[-1]
            if app.job_directory(job) is None:
                raise RuntimeError('Generate did not create a job: '+job)
            record.update(job=job, source_key=key, settings=settings, profile=suggested['profile'],
                          plan=plan['plan'], state='running', started_at=app.utc_now())
            save()
            print(json.dumps({'case':case['id'],'job':job,'plan':plan['plan']},ensure_ascii=True), flush=True)
        last = None
        started = time.monotonic()
        while True:
            status = get('/api/job/'+record['job'])
            current = status.get('state'), status.get('step')
            if current != last:
                print(json.dumps({'case':case['id'], 'state':current[0], 'step':current[1]}, ensure_ascii=True), flush=True)
                last = current
                record.update(status=status, state='running')
                save()
            if status.get('state') in {'done', 'error'} and not status.get('busy'):
                record.update(state=status['state'], status=status, finished_at=app.utc_now(),
                              model=status.get('model'), run_id=status.get('run_id'),
                              quality=status.get('quality',{}), error=status.get('error'))
                save()
                print(json.dumps({'finished':case['id'], 'state':record['state'],
                                  'quality':record['quality'], 'error':record.get('error')}, ensure_ascii=True), flush=True)
                break
            if time.monotonic()-started > 7200:
                raise TimeoutError('Task still active; resume this batch to continue: '+record['job'])
            time.sleep(5)
    print('Results: '+args.base+'/experiments?batch='+args.batch, flush=True)


if __name__ == '__main__':
    main()
