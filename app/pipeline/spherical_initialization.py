"""One bounded initialization retry for short, forward-moving 360 clips."""
from pathlib import Path
import subprocess


def tail_since(log, start):
    log.flush()
    if hasattr(log, 'getvalue'):
        return log.getvalue()[start:][-32768:]
    path = Path(log.name)
    with path.open('rb') as handle:
        handle.seek(max(start, path.stat().st_size-32768))
        return handle.read().decode('utf8', errors='replace')


def run_mapper(command, *, panorama_count, resuming, execute, log, report=lambda metadata:None):
    start = log.tell()
    try:
        return execute(command)
    except subprocess.CalledProcessError:
        if (resuming or panorama_count >= 60 or command[1] != 'mapper'
                or '--Mapper.init_max_forward_motion' in command
                or 'No good initial image pair found' not in tail_since(log, start)):
            raise
    # A spherical camera sees all directions. The pinhole initialization's
    # z-translation filter can reject a valid walk even with strong 360 tracks.
    # Do not lower inlier, triangulation, filtering or final quality thresholds.
    metadata = dict(reason='Short spherical clip: no initial pair with default forward-motion filter',
                    init_max_forward_motion=1.0, attempts=2)
    log.write('Spherical initialization retry: --Mapper.init_max_forward_motion 1; '
              'retain triangulation, inlier and final reconstruction quality checks\n')
    log.flush()
    report(metadata)
    return execute([*command, '--Mapper.init_max_forward_motion', '1'])
