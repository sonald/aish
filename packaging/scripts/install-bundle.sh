#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOTFS_DIR="${SCRIPT_DIR}/rootfs"
MIN_GLIBC_VERSION="2.28"
FORCE_CONFIG_OVERWRITE=0

usage() {
	cat <<'EOF'
Usage: sudo ./install.sh [--force-config]

Installs AI Shell binaries, systemd units, default skills, and the security policy.
EOF
}

version_ge() {
	local current="$1"
	local minimum="$2"
	[[ "$(printf '%s\n%s\n' "$minimum" "$current" | sort -V | head -n1)" == "$minimum" ]]
}

require_root() {
	if [[ "${EUID}" -ne 0 ]]; then
		echo "This installer must run as root." >&2
		exit 1
	fi
}

require_command() {
	local command_name="$1"
	local hint="$2"
	if ! command -v "$command_name" >/dev/null 2>&1; then
		echo "Missing runtime dependency: ${command_name}. ${hint}" >&2
		exit 1
	fi
}

check_glibc() {
	local glibc_version
	if ! glibc_version="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"; then
		echo "Unable to detect glibc version via getconf." >&2
		exit 1
	fi
	if ! version_ge "$glibc_version" "$MIN_GLIBC_VERSION"; then
		echo "glibc ${MIN_GLIBC_VERSION}+ is required. Detected: ${glibc_version}." >&2
		exit 1
	fi
}

check_runtime_dependencies() {
	require_command bwrap "Install the bubblewrap package before continuing."
	require_command unshare "Install util-linux before continuing."
	require_command systemctl "A systemd-based system is required for aish-sandbox.socket."

	if [[ ! -d /run/systemd/system ]]; then
		echo "systemd does not appear to be running on this system." >&2
		exit 1
	fi

	if [[ ! -d "$ROOTFS_DIR" ]]; then
		echo "Bundle payload not found: ${ROOTFS_DIR}" >&2
		exit 1
	fi
}

install_file() {
	local source_path="$1"
	local target_path="$2"
	local mode="$3"
	install -D -m "$mode" "$source_path" "$target_path"
}

install_config() {
	local source_path="$1"
	local target_path="$2"
	if [[ -f "$target_path" && "$FORCE_CONFIG_OVERWRITE" -ne 1 ]]; then
		echo "Preserving existing config: ${target_path}"
		return
	fi
	install_file "$source_path" "$target_path" 0644
}

install_tree() {
	local source_dir="$1"
	local target_dir="$2"
	install -d "$target_dir"
	cp -a "$source_dir/." "$target_dir/"
}

enable_services() {
	systemctl daemon-reload
	systemctl enable aish-sandbox.socket >/dev/null 2>&1 || true
	if systemctl is-active --quiet aish-sandbox.socket || systemctl is-active --quiet aish-sandbox.service; then
		systemctl restart aish-sandbox.socket
	else
		systemctl start aish-sandbox.socket
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--force-config)
			FORCE_CONFIG_OVERWRITE=1
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
check_glibc
check_runtime_dependencies

install_file "$ROOTFS_DIR/usr/bin/aish" "/usr/bin/aish" 0755
install_file "$ROOTFS_DIR/usr/bin/aish-sandbox" "/usr/bin/aish-sandbox" 0755
install_config "$ROOTFS_DIR/etc/aish/security_policy.yaml" "/etc/aish/security_policy.yaml"
install_file "$ROOTFS_DIR/lib/systemd/system/aish-sandbox.service" "/lib/systemd/system/aish-sandbox.service" 0644
install_file "$ROOTFS_DIR/lib/systemd/system/aish-sandbox.socket" "/lib/systemd/system/aish-sandbox.socket" 0644
install_file "$ROOTFS_DIR/usr/share/doc/aish/skills-guide.md" "/usr/share/doc/aish/skills-guide.md" 0644

if [[ -d "$ROOTFS_DIR/usr/share/aish/skills" ]]; then
	install_tree "$ROOTFS_DIR/usr/share/aish/skills" "/usr/share/aish/skills"
fi

enable_services

echo "AI Shell installed successfully."