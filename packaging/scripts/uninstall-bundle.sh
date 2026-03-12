#!/usr/bin/env bash
set -euo pipefail

PURGE_CONFIG=0
INSTALL_ROOT="${AISH_INSTALL_ROOT:-}"
INSTALL_PREFIX=""

usage() {
	cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge-config] [--prefix=PATH]

Removes AI Shell binaries, systemd units, and bundled skills.
EOF
}

require_root() {
	if [[ -n "$INSTALL_ROOT" ]] || [[ -n "$INSTALL_PREFIX" ]]; then
		return
	fi
	if [[ "${EUID}" -ne 0 ]]; then
		echo "This uninstaller must run as root." >&2
		exit 1
	fi
}

target_path() {
	local absolute_path="$1"
	if [[ -n "$INSTALL_ROOT" ]]; then
		printf '%s%s\n' "$INSTALL_ROOT" "$absolute_path"
	elif [[ -n "$INSTALL_PREFIX" ]]; then
		printf '%s%s\n' "$INSTALL_PREFIX" "$absolute_path"
	else
		printf '%s\n' "$absolute_path"
	fi
}

binary_target_dir() {
	printf '%s\n' "/usr/local/bin"
}

disable_services() {
	if command -v systemctl >/dev/null 2>&1; then
		systemctl disable --now aish-sandbox.socket >/dev/null 2>&1 || true
		systemctl stop --no-block aish-sandbox.service >/dev/null 2>&1 || true
		systemctl reset-failed aish-sandbox.service >/dev/null 2>&1 || true
		systemctl daemon-reload >/dev/null 2>&1 || true
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--purge-config)
			PURGE_CONFIG=1
			shift
			;;
		--prefix=*)
			INSTALL_PREFIX="${1#*=}"
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "Unknown option: $1" >&2
			usage >&2
			exit 1
			;;
	esac
done

require_root
disable_services

BIN_DIR="$(binary_target_dir)"

rm -f "$(target_path "${BIN_DIR}/aish")" "$(target_path "${BIN_DIR}/aish-sandbox")"

if [[ -z "$INSTALL_PREFIX" ]]; then
	rm -f "$(target_path "/etc/systemd/system/aish-sandbox.service")" "$(target_path "/etc/systemd/system/aish-sandbox.socket")"
fi

rm -rf "$(target_path "/usr/local/share/aish/skills")"
rm -f "$(target_path "/usr/local/share/aish/skills-guide.md")"

if [[ "$PURGE_CONFIG" -eq 1 ]]; then
	rm -f "$(target_path "/etc/aish/security_policy.yaml")"
	rmdir --ignore-fail-on-non-empty "$(target_path "/etc/aish")" >/dev/null 2>&1 || true
fi

echo "AI Shell removed successfully."