#!/usr/bin/env bash
# Read-only checks. Does not edit boot settings, firewall rules or services.
set -euo pipefail
role=${1:-agent}
mode=${2:-auto}
[[ $role == control || $role == agent ]] || { echo "Usage: $0 control|agent [auto|v1|v2]" >&2; exit 1; }
[[ $mode == auto || $mode == v1 || $mode == v2 ]] || { echo 'Invalid cgroup mode.' >&2; exit 1; }
failed=0
check() { if "$@"; then echo "PASS: $*"; else echo "FAIL: $*"; failed=1; fi; }
cat /etc/os-release
uname -a
check test "$(uname -m)" = riscv64
source /etc/os-release
check test "${ID,,}" = openeuler
check command -v python3
check command -v systemctl
if command -v go >/dev/null; then go version; else echo 'INFO: Go missing; supplied binary can run without Go.'; fi
if [[ $role == agent ]]; then
  check command -v runc
  if command -v runc >/dev/null; then runc --version; fi
  base=$(cd "$(dirname "$0")/.." && pwd)
  binary="$base/bin/rcs-linux-riscv64"
  if [[ ! -x $binary ]]; then binary=$(command -v rcs || true); fi
  if [[ -n $binary && -x $binary ]]; then
    check "$binary" check-cgroups -mode "$mode"
  else
    echo 'FAIL: Need the supplied 0.1.2 Linux binary (chmod +x bin/rcs-linux-riscv64) or compile first.'
    failed=1
  fi
  if command -v findmnt >/dev/null; then findmnt -t cgroup,cgroup2 || true; fi
  cat /proc/cgroups
  check command -v cp
  check command -v chroot
  echo 'INFO: agent installation and runc require root via sudo.'
fi
if command -v getenforce >/dev/null; then getenforce; fi
exit "$failed"
