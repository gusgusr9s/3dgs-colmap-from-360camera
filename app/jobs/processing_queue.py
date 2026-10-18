"""FIFO admission for expensive pipelines, shared by local server processes."""
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time
import uuid
import errno
import os


def try_lock(handle):
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise

_local = threading.local()


class QueueCancelled(RuntimeError):
    pass


@contextmanager
def processing_slot(directory, owner_alive, waiting=None, poll_seconds=.25, cancelled=None, owner_directory=None):
    directory = Path(directory).resolve()
    held = getattr(_local, 'held', set())
    if directory in held:
        yield
        return
    directory.mkdir(parents=True, exist_ok=True)
    ticket = directory / f'{time.time_ns():020d}_{uuid.uuid4().hex}.json'
    with ticket.open('x', encoding='utf-8') as handle:
        json.dump({'pid': os.getpid(), **({'job_directory':str(Path(owner_directory).resolve())} if owner_directory else {})}, handle)
    previous = None
    # OS lock prevents overlap even when ticket creation is delayed or the clock moves.
    # It is released by the OS if a worker/server exits unexpectedly.
    handle = None
    try:
        handle = (directory / '.pipeline.lock').open('a+b')
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        while True:
            if cancelled and cancelled():
                raise QueueCancelled('ยกเลิกการรอคิวแล้ว')
            tickets = []
            for candidate in sorted(directory.glob('*.json')):
                try:
                    if candidate == ticket or owner_alive(candidate):
                        tickets.append(candidate)
                    else:
                        candidate.unlink(missing_ok=True)
                except FileNotFoundError:
                    continue
            if tickets and tickets[0] == ticket and try_lock(handle):
                break
            position = tickets.index(ticket) if ticket in tickets else len(tickets)
            if waiting and position != previous:
                waiting(position)
                previous = position
            time.sleep(poll_seconds)
        _local.held = held | {directory}
        try:
            yield
        finally:
            _local.held = held
    finally:
        if handle is not None:
            handle.close()
        ticket.unlink(missing_ok=True)
