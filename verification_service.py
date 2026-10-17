"""One bounded verification subprocess per server, with an in-memory result."""
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


def start(workspace):
    with _lock:
        if _state.get('state') == 'running':
            return False
        _state.clear()
        _state.update(state='running', started=time.time())
    threading.Thread(target=_run, args=(workspace,), daemon=True).start()
    return True


def _run(workspace):
    try:
        completed = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).parent / 'scripts/verify_environment.py'),
                                    '--json', '--workspace', str(workspace)],
                                   capture_output=True, text=True, encoding='utf8', timeout=70,
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
