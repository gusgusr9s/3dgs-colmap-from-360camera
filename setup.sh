#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"
MODE=install
DEVICE=auto
WITH_3D=1
usage() {
  cat <<'EOF'
Ubuntu 24.04 x86_64 setup (Python 3.12). NVIDIA driver must already work.
Usage: bash setup.sh [--cuda|--cpu] [--check] [--skip-3d]
  --cuda     Build COLMAP with CUDA; install CUDA Torch for person masks.
  --cpu      Build CPU COLMAP; install CPU Torch. Brush still uses the GPU.
  --check    Verify existing installation without installing anything.
  --skip-3d  Install image/view preparation dependencies only.
Overrides: VENV_DIR, CUDA_ARCHITECTURES (default native), BUILD_JOBS (default 2).
EOF
}
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --cuda) DEVICE=cuda ;;
    --cpu) DEVICE=cpu ;;
    --skip-3d) WITH_3D=0 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done
export PATH="${ROOT_DIR}/tools/colmap/bin:${ROOT_DIR}/tools/brush:${PATH}"
export QT_QPA_PLATFORM=offscreen PYTHONUTF8=1

if [[ "$MODE" == check ]]; then
  [[ -x "${VENV_DIR}/bin/python" ]] || { echo 'Missing virtual environment; run bash setup.sh first.' >&2; exit 1; }
  options=(--colmap-device "$DEVICE")
  [[ "$DEVICE" != cuda ]] || options+=(--mask-device cuda)
  [[ "$WITH_3D" == 1 ]] || options+=(--views-only)
  exec "${VENV_DIR}/bin/python" "${ROOT_DIR}/scripts/check_runtime.py" "${options[@]}"
fi

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'This installer requires Linux x86_64.' >&2; exit 1; }
command -v apt-get >/dev/null || { echo 'This installer requires Ubuntu / apt-get.' >&2; exit 1; }
if [[ "$EUID" == 0 ]]; then SUDO=(); else SUDO=(sudo); fi
if [[ "$DEVICE" == auto ]]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then DEVICE=cuda; else DEVICE=cpu; fi
fi
if [[ "$DEVICE" == cuda ]]; then
  command -v nvidia-smi >/dev/null && nvidia-smi -L || { echo 'NVIDIA driver unavailable. Install the driver, reboot, and check nvidia-smi.' >&2; exit 1; }
fi

"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y python3 python3-venv python3-dev python3-pip \
  build-essential cmake ninja-build ffmpeg git curl xz-utils libgl1 libegl1 \
  libvulkan1 mesa-vulkan-drivers vulkan-tools
python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Ubuntu 24.04 / Python 3.12 for this dependency set"'
[[ -x "${VENV_DIR}/bin/python" ]] || python3 -m venv "$VENV_DIR"
PYTHON="${VENV_DIR}/bin/python"
"$PYTHON" -m pip install --upgrade pip setuptools wheel
if [[ "$WITH_3D" == 0 ]]; then
  "$PYTHON" -m pip install 'numpy>=2.0' 'opencv-python>=4.8' 'Pillow>=10.0'
  "$PYTHON" -m pip install -r "${ROOT_DIR}/requirements-gpu.txt"
  "$PYTHON" "${ROOT_DIR}/scripts/check_runtime.py" --views-only
  echo 'View preparation ready. Full web app / 3D requires bash setup.sh.'
  exit 0
fi

# Pin compatible Torch packages; CUDA is used by segmentation, not Brush.
TORCH_VARIANT=cpu
[[ "$DEVICE" != cuda ]] || TORCH_VARIANT=cu128
"$PYTHON" -m pip install --upgrade "torch==2.7.1+${TORCH_VARIANT}" "torchvision==0.22.1+${TORCH_VARIANT}" "torchaudio==2.7.1+${TORCH_VARIANT}" \
  --index-url "https://download.pytorch.org/whl/${TORCH_VARIANT}"
"$PYTHON" -m pip install -r "${ROOT_DIR}/requirements.txt" 'pycolmap==4.2.1'
"$PYTHON" -m pip install -r "${ROOT_DIR}/requirements-gpu.txt"
"$PYTHON" -m pip check
"$PYTHON" "${ROOT_DIR}/scripts/install_viewer_assets.py"
"$PYTHON" -c 'from torchvision.models.segmentation import lraspp_mobilenet_v3_large, LRASPP_MobileNet_V3_Large_Weights; lraspp_mobilenet_v3_large(weights=LRASPP_MobileNet_V3_Large_Weights.DEFAULT)'

# Ubuntu's COLMAP package may be too old for spherical cameras and lacks CUDA.
# Build a pinned CLI locally without replacing the system's COLMAP.
"${SUDO[@]}" apt-get install -y libboost-program-options-dev libboost-graph-dev \
  libboost-system-dev libeigen3-dev libopenimageio-dev openimageio-tools libmetis-dev \
  libgoogle-glog-dev libgtest-dev libgmock-dev libsqlite3-dev libglew-dev \
  qt6-base-dev libqt6opengl6-dev libqt6openglwidgets6 qt6-svg-dev libcgal-dev \
  libceres-dev libsuitesparse-dev libcurl4-openssl-dev libssl-dev libmkl-full-dev
"${SUDO[@]}" mkdir -p /usr/include/opencv4
CUDA_ENABLED=OFF
CUDA_OPTIONS=()
if [[ "$DEVICE" == cuda ]]; then
  export PATH="/usr/local/cuda/bin:${PATH}"
  if ! command -v nvcc >/dev/null; then
    if nvidia-smi --query-gpu=name --format=csv,noheader | grep -q 'RTX 50'; then
      echo 'RTX 50 requires CUDA Toolkit 12.8 or newer. Install it from NVIDIA, then rerun setup.' >&2
      exit 1
    fi
    "${SUDO[@]}" apt-get install -y nvidia-cuda-toolkit nvidia-cuda-toolkit-gcc
  fi
  command -v nvcc >/dev/null || { echo 'nvcc missing: install the NVIDIA CUDA Toolkit.' >&2; exit 1; }
  if nvidia-smi --query-gpu=name --format=csv,noheader | grep -q 'RTX 50'; then
    nvcc --list-gpu-code | grep -q sm_120 || { echo 'This nvcc cannot build for RTX 50; install CUDA Toolkit 12.8 or newer.' >&2; exit 1; }
  fi
  CUDA_ENABLED=ON
  CUDA_OPTIONS+=("-DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHITECTURES:-native}" "-DCMAKE_CUDA_COMPILER=$(command -v nvcc)")
  if [[ -z "${CUDAHOSTCXX:-}" ]] && [[ "$(command -v nvcc)" == /usr/bin/nvcc ]] && command -v cuda-g++ >/dev/null; then
    CUDA_OPTIONS+=("-DCMAKE_CUDA_HOST_COMPILER=$(command -v cuda-g++)")
  fi
fi
COLMAP_SOURCE="${ROOT_DIR}/tools/colmap-src-4.2.1"
COLMAP_BUILD="${ROOT_DIR}/tools/colmap-build-${DEVICE}"
if [[ ! -d "$COLMAP_SOURCE" ]]; then
  git clone --depth 1 --branch 4.2.1 https://github.com/colmap/colmap.git "$COLMAP_SOURCE"
fi
[[ "$(git -C "$COLMAP_SOURCE" describe --tags --exact-match HEAD)" == 4.2.1 ]] || { echo 'Existing COLMAP source is not tag 4.2.1.' >&2; exit 1; }
cmake -S "$COLMAP_SOURCE" -B "$COLMAP_BUILD" -GNinja \
  -DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX=${ROOT_DIR}/tools/colmap" \
  -DGUI_ENABLED=OFF -DTESTS_ENABLED=OFF -DCUDA_ENABLED="$CUDA_ENABLED" -DHIP_ENABLED=OFF \
  -DCOLMAP_HASH_MAP_BACKEND=STD -DBLA_VENDOR=Intel10_64lp "${CUDA_OPTIONS[@]}"
cmake --build "$COLMAP_BUILD" --parallel "${BUILD_JOBS:-2}"
cmake --install "$COLMAP_BUILD"
bash "${ROOT_DIR}/install_brush.sh"

options=(--colmap-device "$DEVICE" --mask-device "$DEVICE")
"$PYTHON" "${ROOT_DIR}/scripts/check_runtime.py" "${options[@]}"
printf '\nSetup complete. Run: bash run_web.sh --port 8000\n'
