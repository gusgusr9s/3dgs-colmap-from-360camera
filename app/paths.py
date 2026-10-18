"""Repository locations shared by modules, workers and development entry points."""

from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = Path(os.environ['STUDIO_WORKSPACE']).expanduser().resolve() if os.environ.get('STUDIO_WORKSPACE') else ROOT
RUNTIME_CACHE = WORKSPACE / 'web_data' / 'cache' if os.environ.get('STUDIO_WORKSPACE') else ROOT / 'tools'
STATIC = ROOT / 'assets' / 'app'
TEMPLATES = ROOT / 'app' / 'web' / 'templates'
DOCS = ROOT / 'docs'
