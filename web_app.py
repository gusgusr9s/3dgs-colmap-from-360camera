#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in app.web.server."""
import sys
from app.runtime.tool_environment import refresh_tool_path

if __name__ == '__main__':
    refresh_tool_path()

from app.web import server as implementation

if __name__ == '__main__':
    implementation.main()
else:
    # Old integrations and new package imports share one module and its state.
    sys.modules[__name__] = implementation
