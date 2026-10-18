"""Explicit tiny GPU BA smoke: spherical dense CUDA + sparse cuDSS; never changes Jobs."""
import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.runtime.colmap_ba import probe, write_receipt
from app.runtime.runtime_verification import colmap_executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--colmap', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'tools/install-logs/colmap-ba-smoke.json')
    parser.add_argument('--write-receipt', action='store_true', help='enable this bundle only after both GPU solves pass')
    args = parser.parse_args()
    executable = args.colmap or colmap_executable()
    if not executable:
        parser.error('COLMAP is missing')
    report = probe(executable, ROOT/'tools/install-logs'/('ba-smoke-'+uuid.uuid4().hex[:8]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    if args.write_receipt and report['ready']:
        write_receipt(executable, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
