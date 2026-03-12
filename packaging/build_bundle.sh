#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VERSION="${VERSION:-${1:-0.1.0}}"
ARCH="${ARCH:-${2:-$(uname -m)}}"
PLATFORM="${PLATFORM:-${4:-linux}}"
OUTPUT_DIR="${OUTPUT_DIR:-${3:-dist/release}}"
BUNDLE_NAME="aish-${VERSION}-${PLATFORM}-${ARCH}"
STAGE_DIR="build/bundle/${BUNDLE_NAME}"
ROOTFS_DIR="${STAGE_DIR}/rootfs"

if [[ ! -x "dist/aish" || ! -x "dist/aish-sandbox" ]]; then
  echo "Binary artifacts are missing, building them first..."
  make build-binary
fi

rm -rf "$STAGE_DIR"
mkdir -p "$ROOTFS_DIR" "$OUTPUT_DIR"

make install NO_BUILD=1 DESTDIR="$ROOTFS_DIR"

install -m 0755 packaging/scripts/install-bundle.sh "${STAGE_DIR}/install.sh"
install -m 0755 packaging/scripts/uninstall-bundle.sh "${STAGE_DIR}/uninstall.sh"

cat > "${STAGE_DIR}/README.txt" <<EOF
AI Shell bundle ${VERSION} (${ARCH})

Install:
  sudo ./install.sh

Uninstall:
  sudo ./uninstall.sh
EOF

tar -C "$(dirname "$STAGE_DIR")" -czf "${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz" "$(basename "$STAGE_DIR")"
sha256sum "${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz" > "${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz.sha256"

echo "Created bundle: ${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz"