#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage: packaging/build_deb.sh [--release] [--no-date] [dpkg-buildpackage args...]

Examples:
  packaging/build_deb.sh
  packaging/build_deb.sh --release
  packaging/build_deb.sh -- -nc

Environment:
  AISH_DEB_APPEND_DATE  Append +YYYYMMDD to the package version (default: 1)
  AISH_DEB_BUILD_DATE   Override the build date suffix used above
EOF
}

EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release)
      export AISH_DEB_APPEND_DATE=0
      shift
      ;;
    --no-date)
      export AISH_DEB_APPEND_DATE=0
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      EXTRA_ARGS+=("$@")
      break
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

dpkg-buildpackage -us -uc -b "${EXTRA_ARGS[@]}"
