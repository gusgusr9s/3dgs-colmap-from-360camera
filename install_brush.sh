#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${ROOT_DIR}/tools/brush"
if [[ -x "${INSTALL_DIR}/brush_app" ]]; then
  exec "${INSTALL_DIR}/brush_app" --version
fi
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Brush installer requires Linux x86_64.' >&2; exit 1; }
ARCHIVE="$(mktemp --suffix=.tar.xz)"
trap 'rm -f -- "$ARCHIVE"' EXIT
curl --fail --location --retry 3 \
  https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-unknown-linux-gnu.tar.xz \
  --output "$ARCHIVE"
printf '4f0f9a8785d1951c62df26aae247c02c5bba32b00f40b06df4e1c9b867399e20  %s\n' "$ARCHIVE" | sha256sum --check -
mkdir -p "$INSTALL_DIR"
tar -xJf "$ARCHIVE" -C "$INSTALL_DIR" --strip-components=1
[[ -f "${INSTALL_DIR}/brush_app" ]] || { echo 'Brush archive did not contain brush_app.' >&2; exit 1; }
chmod +x "${INSTALL_DIR}/brush_app"
"${INSTALL_DIR}/brush_app" --version
