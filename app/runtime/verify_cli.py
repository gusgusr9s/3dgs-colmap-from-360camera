"""Shared verification CLI used by development scripts and packaged workers."""
import argparse
import json
from pathlib import Path


def main():
    from app.runtime.runtime_verification import verify_environment
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--execute', action='store_true',
                        help='also run bounded CPU/GPU work on generated fixtures and benchmark global BA solvers')
    args = parser.parse_args()
    report = verify_environment(args.workspace)
    if args.execute:
        from app.runtime.execution_verification import run_checks
        report.update(run_checks(report, args.workspace))
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"Python: {report['python']} ({report['python_version']})\nOS: {report['os']}")
        print('CPU budget: '+json.dumps(report['cpu_runtime']))
        for stage in report['stages']:
            print(f"[{stage['state']}] {stage['label']}: {stage['backend']} — {stage['reason']}")
        for check in report.get('execution_checks', []):
            print(f"[{check['state']}] {check['label']}: {check['requested_backend']} → {check['actual_backend']} "
                  f"({check['seconds']}s) — {check['detail']}")
        if args.execute and report.get('execution_state') == 'blocked':
            print(report['execution_reason'])
        print(report['scope'])
    ready = report['ready'] and (not args.execute or report.get('execution_ready', False))
    raise SystemExit(0 if ready else 1)


if __name__ == '__main__':
    main()
