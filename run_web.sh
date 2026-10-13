#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"
[[ -x "${VENV_DIR}/bin/python" ]] || { echo 'Missing Python environment. Run bash setup.sh first.' >&2; exit 1; }
export PATH="${VENV_DIR}/bin:${ROOT_DIR}/tools/colmap/bin:${ROOT_DIR}/tools/brush:${PATH}"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export WGPU_BACKEND="${WGPU_BACKEND:-vulkan}"
cd "$ROOT_DIR"
exec "${VENV_DIR}/bin/python" web_app.py "$@"
