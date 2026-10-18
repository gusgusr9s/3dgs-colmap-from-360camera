"""Cooperative job stops and isolated, parent-monitored native commands.

Stop requests carry the job lock token. Never signal a PID supplied by HTTP.
Windows COLMAP gets its own hidden console so Ctrl+C cannot reach the web server.
"""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
from app.paths import ROOT
from app.runtime.python_command import module_command

_local = threading.local()


class TaskStopped(RuntimeError):
    pass


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}


def write(path, record):
    path = Path(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf8')
        for attempt in range(10):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.02)
    finally:
        temporary.unlink(missing_ok=True)


def identity(pid):
    """Process creation identity, protecting against reuse of a recorded PID."""
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            values = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                return None
            if kernel.GetProcessTimes(handle, *[ctypes.byref(v) for v in values]):
                return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
        return None
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, ValueError, IndexError):
        return None


def child_active(directory):
    record = read(Path(directory)/'.controlled_process.json')
    return any(record.get(key+'_identity') and identity(record.get(key, 0)) == record[key+'_identity']
               for key in ('worker_pid', 'child_pid'))


@contextmanager
def scope(directory, token):
    previous = getattr(_local, 'context', None)
    if previous or not token:
        yield
        return
    directory = Path(directory)
    _local.context = (directory, token)
    write(directory/'.task_control.json', {'token': token, 'owner_pid': os.getpid()})
    try:
        yield
    finally:
        _local.context = previous


def requested(directory, token):
    return bool(token) and read(Path(directory)/'.stop_request.json').get('token') == token


def check_stop():
    context = getattr(_local, 'context', None)
    if context and requested(*context):
        raise TaskStopped('หยุดตามคำขอแล้ว · เก็บข้อมูลที่ทำเสร็จไว้')


def request_stop(directory, token):
    directory = Path(directory)
    if not token or read(directory/'.task_control.json').get('token') != token:
        raise ValueError('งานนี้เริ่มจากแอปรุ่นเดิม ยังไม่รองรับปุ่มหยุดอย่างปลอดภัย')
    write(directory/'.stop_request.json', {'token': token, 'requested_at': time.time()})


def controls(directory, token):
    enabled = bool(token) and read(Path(directory)/'.task_control.json').get('token') == token
    return {'can_stop': enabled, 'stop_requested': enabled and requested(directory, token),
            'stop_token': token if enabled else None}


def enabled():
    return bool(getattr(_local, 'context', None))


def run(command, **kwargs):
    context = getattr(_local, 'context', None)
    if not context:
        return subprocess.run(command, **kwargs)
    check_stop()
    directory, token = context
    spec = directory/('.native-'+uuid.uuid4().hex+'.json')
    # Mapper saves its sparse model; Brush retains completed periodic exports.
    # Other commands finish their current stage. Parent death requests shutdown.
    write(spec, {'command': list(map(str, command)), 'cwd': str(kwargs.get('cwd', Path.cwd())),
                 'directory': str(directory.resolve()), 'token': token,
                 'owner_pid': os.getpid(), 'owner_identity': identity(os.getpid()),
                 'interruptible': (len(command)>1 and command[1]=='mapper') or '--export-every' in command})
    startup = {}
    if os.name == 'nt':
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        startup = {'creationflags': subprocess.CREATE_NEW_CONSOLE, 'startupinfo': info}
    else:
        startup = {'start_new_session': True}
    try:
        environment = dict(kwargs.get('env') or os.environ)
        environment['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(
            [str(ROOT), *(str(Path(p).resolve()) for p in sys.path if p)]))
        # Windows venv python.exe is a redirector. Ctrl+C can kill that redirector
        # before the real worker finishes saving. Launch the actual interpreter
        # with this environment's import paths so wait() tracks the worker itself.
        interpreter = (getattr(sys,'_base_executable',sys.executable)
                       if os.name=='nt' and not getattr(sys,'frozen',False) else sys.executable)
        worker = subprocess.Popen(module_command('app.jobs.task_control','--worker',spec.resolve(),executable=interpreter),
                                  stdout=kwargs.get('stdout'), stderr=kwargs.get('stderr'),
                                  env=environment, cwd=kwargs.get('cwd'), **startup)
        code = worker.wait()
        if (len(command)>1 and command[1]=='mapper') or '--export-every' in command:
            check_stop()
        if kwargs.get('check') and code:
            raise subprocess.CalledProcessError(code, command)
        return subprocess.CompletedProcess(command, code)
    finally:
        spec.unlink(missing_ok=True)


def supervise(spec_path):
    spec = read(spec_path)
    directory = Path(spec['directory'])
    record = {'token': spec['token'], 'worker_pid': os.getpid(), 'worker_pid_identity': identity(os.getpid()),
              'command': spec['command'], 'interruptible': spec['interruptible']}
    # Register before creating the native child. If the server dies during
    # startup, another server can still see who is saving/holding this job.
    write(directory/'.controlled_process.json',record)
    if identity(spec['owner_pid']) != spec['owner_identity']:
        return 130
    child = subprocess.Popen(spec['command'], cwd=spec['cwd'], stdout=sys.stdout, stderr=sys.stderr)
    # Python handles its own Ctrl+C without an inheritable ignore attribute.
    # COLMAP has already started in this isolated console/session.
    signal.signal(signal.SIGINT, lambda *_: None)
    record.update(child_pid=child.pid, child_pid_identity=identity(child.pid))
    write(directory/'.controlled_process.json', record)
    sent = False
    last_exports = None
    while child.poll() is None:
        owner_gone = identity(spec['owner_pid']) != spec['owner_identity']
        stopping = requested(directory, spec['token'])
        if '--export-path' in spec['command']:
            try:
                from app.pipeline.training_recovery import prune
                export = Path(spec['command'][spec['command'].index('--export-path')+1])
                stamps = tuple((p.name,p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(export.glob('splat_*.ply')))
                if stamps != last_exports:
                    prune(export)
                    last_exports = stamps
            except (OSError, ValueError) as exc:
                print('[control] Checkpoint maintenance: '+str(exc),flush=True)
        if not sent and (owner_gone or (stopping and spec['interruptible'])):
            # One graceful signal only. Never escalate to kill on a timer.
            try:
                if os.name == 'nt':
                    if not ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0):
                        raise ctypes.WinError()
                else:
                    child.send_signal(signal.SIGINT)
                sent = True
                print('[control] Graceful interrupt requested'+(' (parent exited)' if owner_gone else ''),flush=True)
                write(directory/'.controlled_process.json', {**record, 'signal_sent_at': time.time()})
            except OSError as exc:
                write(directory/'.controlled_process.json', {**record, 'signal_error': str(exc)})
                sent = True
        time.sleep(.25)
    write(directory/'.controlled_process.json', {**record, 'returncode': child.returncode, 'finished_at': time.time()})
    return child.returncode


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--worker':
        raise SystemExit('Internal worker: task_control.py --worker <spec>')
    raise SystemExit(supervise(sys.argv[2]))
