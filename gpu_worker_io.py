"""Read worker progress with a bounded wait; only terminate the child we own."""
from contextlib import contextmanager
from queue import Queue, Empty
import subprocess
import threading

from gpu_projection import GPUUnavailable


@contextmanager
def worker_lines(process, timeout=120, label='GPU'):
    messages=Queue()
    def read():
        try:
            for line in process.stdout:messages.put(line)
        except Exception as exc:
            messages.put(exc)
        finally:messages.put(None)
    thread=threading.Thread(target=read,daemon=True);thread.start()
    def lines():
        while True:
            try:item=messages.get(timeout=timeout)
            except Empty as exc:raise GPUUnavailable(f'{label} worker timed out without progress for {timeout}s') from exc
            if item is None:break
            if isinstance(item,Exception):raise item
            yield item
    try:
        yield lines()
        try:process.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:raise GPUUnavailable(f'{label} worker did not exit after closing progress') from exc
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        thread.join(timeout=5)
        process.stdout.close()
