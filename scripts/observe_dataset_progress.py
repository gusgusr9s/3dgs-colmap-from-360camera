"""Bridge live dataset logs for a run started before image callbacks existed.

Read-only observations of the dataset; append log events only. Never change
status, training inputs or process state. Exit when this run/phase finishes.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.jobs.task_control import read


def append_line(path,text):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o666)
    try:
        os.write(descriptor,text.encode('utf8'))
    finally:
        os.close(descriptor)


def observe(job,run,total,interval=5):
    job=Path(job).resolve()
    directory=(job/'model_runs'/run).resolve()
    if directory.parent != job/'model_runs' or total < 1 or interval < 1:
        raise ValueError('Invalid run/total/interval')
    initial=read(directory/'status.json')
    started=initial.get('started_at')
    while True:
        status=read(directory/'status.json')
        current=read(job/'3d_status.json')
        if (status.get('state')!='running' or status.get('phase')!='dataset'
                or status.get('started_at')!=started or current.get('run_id')!=run):
            break
        count=sum(1 for _ in (job/'brush_data/images').glob('*.jpg'))
        at=datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        row={'at':at,'event':'observed_progress','stage':'model','run_id':run,'phase':'dataset',
             'files_present':count,'source_views':total,'percent':round(100*count/total,1),
             'measurement':'filesystem count; images may still be writing; not a validation result'}
        append_line(directory/'process.jsonl',json.dumps(row,ensure_ascii=False)+'\n')
        append_line(directory/'process.log',
            f'[{at}] observed_progress · เตรียม dataset · พบไฟล์ภาพ {count:,}/{total:,} · {100*count/total:.1f}% · ตรวจจากไฟล์จริง\n')
        print(json.dumps(row,ensure_ascii=True),flush=True)
        time.sleep(interval)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',required=True,type=Path)
    parser.add_argument('--run',required=True)
    parser.add_argument('--total',required=True,type=int)
    parser.add_argument('--interval',type=float,default=5)
    args=parser.parse_args()
    observe(args.job,args.run,args.total,args.interval)
