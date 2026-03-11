#!/usr/bin/env bash
set -euo pipefail

PURGE_CONFIG=0

usage() {
	cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge-config]

Removes AI Shell binaries, systemd units, and bundled skills.
EOF
}

require_root() {
	if [[ "${EUID}" -ne 0 ]]; then
		echo "This uninstaller must run as root." >&2
		exit 1
	fi
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

rm -f /usr/bin/aish /usr/bin/aish-sandbox
rm -f /lib/systemd/system/aish-sandbox.service /lib/systemd/system/aish-sandbox.socket
rm -rf /usr/share/aish/skills
rm -f /usr/share/doc/aish/skills-guide.md

if [[ "$PURGE_CONFIG" -eq 1 ]]; then
	rm -f /etc/aish/security_policy.yaml
	rmdir --ignore-fail-on-non-empty /etc/aish >/dev/null 2>&1 || true
fi

echo "AI Shell removed successfully."