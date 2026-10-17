#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION=v24.22.0
ARCHIVE="$(mktemp --suffix=.tar.xz)"
trap 'rm -f -- "$ARCHIVE"' EXIT
curl --fail --location --retry 3 "https://nodejs.org/dist/${VERSION}/node-${VERSION}-linux-x64.tar.xz" -o "$ARCHIVE"
printf 'cc2f601cee82d9a5a233302fe307abc600727035fd7d2c3a3571755989e95008  %s\n' "$ARCHIVE" | sha256sum --check -
mkdir -p "${ROOT_DIR}/tools/node"
tar -xJf "$ARCHIVE" -C "${ROOT_DIR}/tools/node" --strip-components=1
"${ROOT_DIR}/tools/node/bin/node" --version
