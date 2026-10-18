"""Read-only environment verification; safe to run while a model is training."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runtime.runtime_verification import verify_environment

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--workspace', type=Path)
    args = parser.parse_args()
    report = verify_environment(args.workspace)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"Python: {report['python']} ({report['python_version']})\nOS: {report['os']}")
        print('CPU budget: '+json.dumps(report['cpu_runtime']))
        for stage in report['stages']:
            print(f"[{stage['state']}] {stage['label']}: {stage['backend']} — {stage['reason']}")
        print(report['scope'])
    raise SystemExit(0 if report['ready'] else 1)
