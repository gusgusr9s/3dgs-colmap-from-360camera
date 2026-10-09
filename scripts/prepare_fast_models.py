"""Create fast downloads without changing or retraining published models."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_variants import build_fast_variant

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('models', nargs='+', type=Path)
    args = parser.parse_args()
    for model in args.models:
        print(json.dumps(build_fast_variant(model)), flush=True)
