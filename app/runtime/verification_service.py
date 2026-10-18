"""One bounded verification subprocess per server, with an in-memory result."""

from app.paths import ROOT
from app.runtime.python_command import module_command
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

_lock = threading.Lock()
_state = {'state': 'idle'}


def status():
    with _lock:
        result = deepcopy(_state)
    if result.get('state') == 'running':
        result['elapsed_seconds'] = round(time.time() - result['started'], 1)
    return result


def start(workspace, execute=False):
    with _lock:
        if _state.get('state') == 'running':
            return False
        _state.clear()
        _state.update(state='running', started=time.time(), execution_requested=bool(execute))
    threading.Thread(target=_run, args=(workspace, execute), daemon=True).start()
    return True


def _run(workspace, execute=False):
    try:
        arguments = ['--json', '--workspace', workspace, *(['--execute'] if execute else [])]
        # Execution mode runs real CPU/GPU fixtures and times every BA solver.
        completed = subprocess.run(module_command('app.runtime.verify_cli', *arguments), cwd=ROOT,
                                   capture_output=True, text=True, encoding='utf8', timeout=900 if execute else 70,
                                   env={**os.environ, 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'},
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        report = json.loads(completed.stdout)
        if completed.returncode not in (0, 1) or report.get('schema') != 1:
            raise RuntimeError('Verification returned an invalid report')
    except Exception as exc:
        report = {'state': 'error', 'error': str(exc)}
    with _lock:
        _state.clear()
        _state.update(report)
