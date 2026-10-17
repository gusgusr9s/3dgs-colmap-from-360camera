"""Append-only task events and atomic, per-run timing summaries."""
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time
import uuid
import subprocess

from task_estimates import phase
from task_history import utc_now


class ProcessLog:
    def __init__(self, directory, stage, identifier, cpu=None):
        self.directory = Path(directory)
        self.stage, self.identifier, self.cpu = stage, identifier, cpu
        self.started_at, self.started_tick = utc_now(), time.monotonic()
        self.current = None
        self.phases = []
        self.closed = False
        self.last_progress = 0
        self.last_summary = None
        self.lock = threading.RLock()

    def event(self, kind, **values):
        self.directory.mkdir(parents=True, exist_ok=True)
        row = {'at':utc_now(), 'event':kind, 'stage':self.stage, 'run_id':self.identifier, **values}
        with (self.directory/'process.jsonl').open('a', encoding='utf8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False)+'\n')
        details = ' · '.join(f'{key}={value}' for key,value in values.items() if value is not None)
        with (self.directory/'process.log').open('a', encoding='utf8') as handle:
            handle.write(f'[{row["at"]}] {kind} · {details}\n')

    def finish_phase(self, state):
        if self.current is None:
            return
        entry = {**self.current, 'finished_at':utc_now(), 'state':state,
                 'elapsed_seconds':round(time.monotonic()-self.phase_tick, 3)}
        self.phases.append(entry)
        self.event('phase_finished', **entry)
        self.current = None

    def summary(self, state):
        value = {'stage':self.stage, 'run_id':self.identifier, 'state':state,
                 'started_at':self.started_at, 'finished_at':utc_now() if self.closed else None,
                 'elapsed_seconds':round(time.monotonic()-self.started_tick, 3), 'cpu_runtime':self.cpu,
                 'phases':self.phases, 'current_phase':self.current}
        path=self.directory/'process_summary.json'
        temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
            for attempt in range(10):
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    if attempt==9:raise
                    time.sleep(.02)
        finally:
            temporary.unlink(missing_ok=True)
        return value

    def update(self, status):
        with self.lock:
            if self.closed:
                return
            state=status.get('state','running')
            current=phase(status)
            terminal=state in {'done','ready','error','cancelled'}
            changed=False
            if not self.directory.exists():
                self.event('run_started', cpu_runtime=self.cpu)
            elif not (self.directory/'process.jsonl').exists():
                self.event('run_started', cpu_runtime=self.cpu)
            if not terminal and (self.current is None or self.current['phase'] != current):
                changed=True
                self.finish_phase('done')
                self.current={'phase':current, 'started_at':utc_now(), 'step':status.get('step','')}
                self.phase_tick=time.monotonic()
                self.event('phase_started', **self.current)
            if terminal:
                changed=True
                self.finish_phase('error' if state=='error' else state)
                self.closed=True
                self.event('run_finished', state=state, elapsed_seconds=round(time.monotonic()-self.started_tick,3),
                           step=status.get('step'),frames=status.get('frames'),training_steps=status.get('training_steps'),
                           error=str(status.get('error',''))[:2000] or None)
            elif time.monotonic()-self.last_progress>=5:
                changed=True
                fields={key:status[key] for key in ('step','progress','frames','mask_images_written','mask_images_total','work_done','work_total') if key in status}
                self.event('progress', **fields)
                self.last_progress=time.monotonic()
            if changed or self.last_summary is None:
                self.last_summary=self.summary(state)
            return self.last_summary


@contextmanager
def command_timing(log, command):
    started=utc_now();tick=time.monotonic()
    def write(kind, **details):
        log.write('[process] '+json.dumps({'at':utc_now(),'event':kind,'command':list(map(str,command)),**details},ensure_ascii=False)+'\n')
        log.flush()
    write('command_started', started_at=started)
    try:
        yield
    except BaseException as exc:
        write('command_finished', started_at=started, elapsed_seconds=round(time.monotonic()-tick,3),
              state='error',error=str(exc))
        raise
    else:
        write('command_finished', started_at=started, elapsed_seconds=round(time.monotonic()-tick,3),state='done')


def run_logged(command, **kwargs):
    with command_timing(kwargs['stdout'],command):
        return subprocess.run(command,**kwargs)
