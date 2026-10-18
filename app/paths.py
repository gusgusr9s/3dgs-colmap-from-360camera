"""Repository locations shared by modules, workers and development entry points."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'assets' / 'app'
TEMPLATES = ROOT / 'app' / 'web' / 'templates'
DOCS = ROOT / 'docs'
