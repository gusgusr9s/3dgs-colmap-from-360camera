"""Private protocol: only progress/compute events travel to the web process."""
import json
from pathlib import Path
import sys

from gpu_projection import GPUUnavailable
from view_compute import EVENT_PREFIX


def event(**value):
    print(EVENT_PREFIX + json.dumps(value), flush=True)


if __name__ == "__main__":
    from web_app import process_file
    try:
        count = process_file(Path(sys.argv[1]), Path(sys.argv[2]), **json.loads(sys.argv[3]),
            view_device="wgpu", _gpu_worker=True,
            progress_callback=lambda count: event(count=count), compute_callback=lambda metadata: event(compute=metadata))
        event(complete=count)
    except GPUUnavailable as exc:
        event(gpu_error=str(exc))
        sys.exit(82)
    except Exception as exc:
        event(error=str(exc))
        sys.exit(1)
