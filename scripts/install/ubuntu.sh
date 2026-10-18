#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"
MODE=install
DEVICE=auto
usage() {
  echo 'Usage: bash app_1_install.sh [--cpu|--cuda] [--check|--plan]'
  echo 'Ubuntu 24.04 x86_64 / Python 3.12. Default: detect NVIDIA, otherwise CPU COLMAP/masks.'
  echo 'Installs all app prerequisites, then Verify + isolated HTTP smoke test.'
  echo 'Overrides: VENV_DIR, BUILD_JOBS (default 2), CUDA_ARCHITECTURES (default native).'
}
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;; --plan) MODE=plan ;; --cuda) DEVICE=cuda ;; --cpu) DEVICE=cpu ;;
    -h|--help) usage; exit 0 ;; *) usage >&2; printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done
if [[ "$MODE" == plan ]]; then
  echo 'PLAN ONLY: Ubuntu libraries -> Python 3.12 venv -> pinned Python libraries -> Node.js -> COLMAP CPU/CUDA -> Brush -> viewer/export assets -> mask weights -> Verify + HTTP smoke test.'
  printf 'Environment: %s; device: %s. No downloads, installs or jobs started.\n' "$VENV_DIR" "$DEVICE"
  exit 0
fi
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Requires Ubuntu 24.04 x86_64.' >&2; exit 1; }
# shellcheck source=/dev/null
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo 'This dependency set requires Ubuntu 24.04.' >&2; exit 1; }
export PATH="${VENV_DIR}/bin:${ROOT_DIR}/tools/node/bin:${ROOT_DIR}/tools/colmap/bin:${ROOT_DIR}/tools/brush:${PATH}"
export QT_QPA_PLATFORM=offscreen PYTHONUTF8=1 PYTHONIOENCODING=utf-8
if [[ "$MODE" == check ]]; then
  [[ -x "${VENV_DIR}/bin/python" ]] || { echo 'Run bash app_1_install.sh first.' >&2; exit 1; }
  exec "${VENV_DIR}/bin/python" "${ROOT_DIR}/scripts/install/manage.py" verify --device "$DEVICE"
fi
if command -v python3 >/dev/null; then
  python3 "${ROOT_DIR}/scripts/install/manage.py" guard
else
  for lock in "${ROOT_DIR}"/web_data/jobs/*/.task.lock; do
    [[ ! -e "$lock" ]] || { echo 'Existing task lock found; cannot safely install without Python.' >&2; exit 1; }
  done
fi
mkdir -p "${ROOT_DIR}/tools/install-logs"
exec > >(tee -a "${ROOT_DIR}/tools/install-logs/ubuntu-$(date +%Y%m%d-%H%M%S).log") 2>&1
trap 'printf "INSTALL FAILED at line %s. Fix the error above and rerun app_1_install.sh.\n" "$LINENO" >&2' ERR
if [[ "$EUID" == 0 ]]; then SUDO=(); else SUDO=(sudo); fi
if [[ "$DEVICE" == auto ]]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then DEVICE=cuda; else DEVICE=cpu; fi
fi
if [[ "$DEVICE" == cuda ]]; then
  command -v nvidia-smi >/dev/null && nvidia-smi -L || { echo 'NVIDIA driver unavailable. Install vendor driver, reboot, then rerun.' >&2; exit 1; }
fi
echo '[1/7] Installing system libraries...'
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y software-properties-common
"${SUDO[@]}" add-apt-repository -y universe
"${SUDO[@]}" add-apt-repository -y multiverse
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y python3.12 python3.12-venv python3.12-dev python3-pip \
  build-essential cmake ninja-build ffmpeg git curl ca-certificates xz-utils libgl1 libegl1 \
  libvulkan1 mesa-vulkan-drivers vulkan-tools libgomp1 libglib2.0-0 libsm6 libxext6 libxrender1 \
  libboost-program-options-dev libboost-graph-dev libboost-system-dev libeigen3-dev \
  libopenimageio-dev openimageio-tools libmetis-dev libgoogle-glog-dev libgtest-dev libgmock-dev \
  libsqlite3-dev libglew-dev qt6-base-dev libqt6opengl6-dev libqt6openglwidgets6 qt6-svg-dev \
  libcgal-dev libceres-dev libsuitesparse-dev libcurl4-openssl-dev libssl-dev libmkl-full-dev
"${SUDO[@]}" mkdir -p /usr/include/opencv4
echo '[2/7] Preparing Python environment...'
[[ -x "${VENV_DIR}/bin/python" ]] || python3.12 -m venv "$VENV_DIR"
PYTHON="${VENV_DIR}/bin/python"
"$PYTHON" "${ROOT_DIR}/scripts/install/manage.py" python --device "$DEVICE"
echo '[3/7] Preparing Node.js and COLMAP...'
if ! command -v node >/dev/null || ! node -e 'process.exit(Number(process.versions.node.split(".")[0])>=20?0:1)'; then
  bash "${ROOT_DIR}/scripts/install/node.sh"
fi
CUDA_ENABLED=OFF
CUDA_OPTIONS=()
if [[ "$DEVICE" == cuda ]]; then
  export PATH="/usr/local/cuda/bin:${PATH}"
  if nvidia-smi --query-gpu=name --format=csv,noheader | grep -q 'RTX 50'; then
    if ! command -v nvcc >/dev/null || ! nvcc --list-gpu-code | grep -q sm_120; then
      # Toolkit-only package: preserve the installed NVIDIA driver.
      keyring="$(mktemp --suffix=.deb)"
      curl --fail --location --retry 3 https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb -o "$keyring"
      "${SUDO[@]}" dpkg -i "$keyring"
      rm -f -- "$keyring"
      "${SUDO[@]}" apt-get update
      "${SUDO[@]}" apt-get install -y cuda-toolkit-12-8
      export PATH="/usr/local/cuda-12.8/bin:${PATH}"
    fi
  elif ! command -v nvcc >/dev/null; then
    "${SUDO[@]}" apt-get install -y nvidia-cuda-toolkit nvidia-cuda-toolkit-gcc
  fi
  command -v nvcc >/dev/null || { echo 'CUDA Toolkit nvcc missing.' >&2; exit 1; }
  CUDA_ENABLED=ON
  CUDA_OPTIONS+=("-DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHITECTURES:-native}" "-DCMAKE_CUDA_COMPILER=$(command -v nvcc)")
  if [[ -z "${CUDAHOSTCXX:-}" ]] && [[ "$(command -v nvcc)" == /usr/bin/nvcc ]] && command -v cuda-g++ >/dev/null; then
    CUDA_OPTIONS+=("-DCMAKE_CUDA_HOST_COMPILER=$(command -v cuda-g++)")
  fi
fi
COLMAP_SOURCE="${ROOT_DIR}/tools/colmap-src-4.2.1"
COLMAP_BUILD="${ROOT_DIR}/tools/colmap-build-${DEVICE}"
if [[ ! -d "$COLMAP_SOURCE" ]]; then git clone --depth 1 --branch 4.2.1 https://github.com/colmap/colmap.git "$COLMAP_SOURCE"; fi
[[ "$(git -C "$COLMAP_SOURCE" describe --tags --exact-match HEAD)" == 4.2.1 ]] || { echo 'Existing COLMAP source is not tag 4.2.1.' >&2; exit 1; }
cmake -S "$COLMAP_SOURCE" -B "$COLMAP_BUILD" -GNinja \
  -DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX=${ROOT_DIR}/tools/colmap" \
  -DGUI_ENABLED=OFF -DTESTS_ENABLED=OFF -DCUDA_ENABLED="$CUDA_ENABLED" -DHIP_ENABLED=OFF \
  -DCOLMAP_HASH_MAP_BACKEND=STD -DBLA_VENDOR=Intel10_64lp "${CUDA_OPTIONS[@]}"
cmake --build "$COLMAP_BUILD" --parallel "${BUILD_JOBS:-2}"
cmake --install "$COLMAP_BUILD"
echo '[4/7] Installing Brush...'
bash "${ROOT_DIR}/scripts/install/brush.sh"
echo '[5/7] Preparing local assets and mask weights...'
"$PYTHON" "${ROOT_DIR}/scripts/install/manage.py" assets
echo '[6/7] Verifying packages, tools, GPU backends and HTTP startup...'
"$PYTHON" "${ROOT_DIR}/scripts/install/manage.py" verify --device "$DEVICE" --smoke --execute --output "${ROOT_DIR}/tools/install-logs/verify.json"
echo '[7/7] Ready. Run: bash app_2_run.sh'
