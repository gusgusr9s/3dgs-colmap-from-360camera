"""Export a reproducible comparison without launching training or changing models."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark_comparison import build_report, markdown_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=Path, default=ROOT / "web_data" / "jobs")
    parser.add_argument("--output", type=Path, default=ROOT / "GPU_COMPARISON.md")
    args = parser.parse_args()
    report = build_report(args.jobs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown_report(report), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Compared {len(report['measured'])} completed runs: {args.output}")
