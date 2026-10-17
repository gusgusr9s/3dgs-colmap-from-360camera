"""Private CPU worker. Configure native libraries before importing NumPy/OpenCV."""
import json
import os
from pathlib import Path
import sys
from cpu_runtime import cpu_budget

if __name__ == '__main__':
    # Parallel views own the CPU budget; each native remap/encoder stays single-threaded.
    budget=cpu_budget()
    os.environ.update(budget.environment(native_threads=1))
    from cpu_views import convert
    import cv2
    cv2.setNumThreads(1)
    def event(**value):print('@views:'+json.dumps(value),flush=True)
    try:
        count=convert(Path(sys.argv[1]),Path(sys.argv[2]),json.loads(sys.argv[3]),
                      progress=lambda count:event(count=count),compute=lambda value:event(compute=value))
        event(complete=count)
    except Exception as exc:
        event(error=str(exc));raise SystemExit(1)
