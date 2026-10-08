#!/usr/bin/env bash
set -Eeuo pipefail

# Legacy Linux setup for the 360 -> COLMAP -> Gaussian Splatting pipeline.
# Native Windows: use setup_windows.ps1. Training uses Brush/WGPU, not Torch HIP/CUDA.
# Usage: ./setup.sh | ./setup.sh --skip-3d | ./setup.sh --check

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
MODE="install"
WITH_3D=1
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --skip-3d) WITH_3D=0 ;;
    -h|--help) sed -n '1,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[ ok ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
missing=()
check_cmd() { command -v "$1" >/dev/null 2>&1 && ok "$1: $(command -v "$1")" || { warn "$1: missing"; missing+=("$1"); }; }

info "Checking operating system and tools"
check_cmd python3
check_cmd pip3
check_cmd ffmpeg
check_cmd git
check_cmd cmake
check_cmd colmap
info "COLMAP pose estimation uses CPU; Gaussian training uses Brush/WGPU"

if [[ "$MODE" == "check" ]]; then
  info "Checking Python packages in ${VENV_DIR}"
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    "${VENV_DIR}/bin/python" - <<'PY'
mods = ['numpy', 'cv2', 'PIL', 'torch', 'pycolmap', 'nerfstudio', 'gsplat']
for name in mods:
    try:
        mod = __import__(name)
        print(f"[ ok ] {name}: {getattr(mod, '__version__', 'installed')}")
    except Exception as exc:
        print(f"[fail] {name}: {exc.__class__.__name__}")
PY
  else
    warn "Virtual environment does not exist: ${VENV_DIR}"
  fi
  exit 0
fi

if [[ ${#missing[@]} -gt 0 ]]; then
  if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    info "Installing missing system packages with apt"
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-dev build-essential cmake ffmpeg git
    if printf '%s\n' "${missing[@]}" | grep -qx colmap; then
      if apt-cache show colmap >/dev/null 2>&1; then
        sudo apt-get install -y colmap
      else
        warn "COLMAP is not available in this apt repository; install it from https://colmap.github.io/install.html"
      fi
    fi
  else
    warn "Cannot auto-install system packages (apt-get/sudo unavailable): ${missing[*]}"
  fi
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  info "Creating Python virtual environment at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi
PYTHON="${VENV_DIR}/bin/python"
PIP=("${PYTHON}" -m pip)
"${PIP[@]}" install --upgrade pip setuptools wheel

if [[ "$WITH_3D" -eq 1 ]]; then
  info "Installing Python dependencies for COLMAP and Gaussian Splatting"
  info "Installing PyTorch for CPU-side data processing; Brush handles GPU training"
  "${PIP[@]}" install torch torchvision torchaudio
  "${PIP[@]}" install -r "${ROOT_DIR}/requirements.txt"
else
  info "Installing core Python dependencies only"
  "${PIP[@]}" install 'numpy>=2.0' 'opencv-python>=4.8' 'Pillow>=10.0'
fi

info "Running dependency verification"
"${PYTHON}" - <<'PY'
import importlib.util
for name in ['numpy', 'cv2', 'PIL', 'torch', 'pycolmap', 'nerfstudio', 'gsplat']:
    print(f"[{' ok ' if importlib.util.find_spec(name) else 'fail'}] python: {name}")
PY
for command in ffmpeg colmap; do
  if command -v "$command" >/dev/null 2>&1; then ok "$command ready"; else warn "$command still missing"; fi
done

cat <<EOF

Setup complete.
Activate: source "${VENV_DIR}/bin/activate"
Run web:  python web_app.py --port 8000
Check:     ./setup.sh --check
EOF
