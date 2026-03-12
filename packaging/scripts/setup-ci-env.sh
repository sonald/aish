#!/usr/bin/env bash
set -euo pipefail

install_python() {
	local manager="$1"
	local version=""

	for candidate in 3.11 3.10; do
		if "$manager" info -q "python${candidate}" >/dev/null 2>&1; then
			version="$candidate"
			break
		fi
	done

	if [[ -z "$version" ]]; then
		echo "Python 3.10+ not found in repositories" >&2
		exit 1
	fi

	"$manager" install -y "python${version}" "python${version}-devel" "python${version}-pip"
	ln -sf "/usr/bin/python${version}" /usr/local/bin/python
	ln -sf "/usr/bin/pip${version}" /usr/local/bin/pip
}

if command -v dnf >/dev/null 2>&1; then
	dnf install -y bubblewrap util-linux tar gzip curl make findutils
	install_python dnf
elif command -v yum >/dev/null 2>&1; then
	yum install -y bubblewrap util-linux tar gzip curl make findutils
	install_python yum
else
	echo "No dnf/yum found in container" >&2
	exit 1
fi

python -m pip install --upgrade pip setuptools wheel uv
python --version
python -c 'import sys; print(sys.executable)'