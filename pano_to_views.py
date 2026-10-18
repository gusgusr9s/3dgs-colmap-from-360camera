#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in app.pipeline.pano_to_views."""
import sys
from app.pipeline import pano_to_views as implementation

if __name__ == '__main__':
    implementation.main()
else:
    # Old integrations and new package imports share one module and its state.
    sys.modules[__name__] = implementation
