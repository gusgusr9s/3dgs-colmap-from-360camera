"""Entry point for the bundled Python runtime and its private child workers."""
import multiprocessing
import runpy
import sys
from pathlib import Path

if not getattr(sys,'frozen',False):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

WORKERS=frozenset(('app.runtime.cpu_view_worker','app.runtime.view_worker','app.runtime.mask_compute',
                   'app.runtime.capture_preview_worker','app.runtime.pose_preview_worker','app.runtime.verify_cli','app.jobs.task_control'))

if __name__=='__main__':
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf8')
    multiprocessing.freeze_support()
    if len(sys.argv)>2 and sys.argv[1]=='--worker-module':
        module=sys.argv[2]
        if module not in WORKERS:raise SystemExit('Unknown private worker')
        sys.argv=[module,*sys.argv[3:]]
        runpy.run_module(module,run_name='__main__')
    else:
        from app.desktop.backend import main
        main()
