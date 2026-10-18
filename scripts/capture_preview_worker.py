"""Compatibility entry for the isolated capture preview worker."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runtime.capture_preview_worker import main

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
