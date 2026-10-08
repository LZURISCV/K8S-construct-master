#!/usr/bin/env bash
set -euo pipefail
base=$(cd "$(dirname "$0")/.." && pwd)
[[ $EUID -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
if [[ $# -lt 4 ]]; then
  echo "Usage: $0 control BINARY CONFIG ADMIN_TOKEN NODE_TOKEN" >&2
  echo "       $0 agent BINARY CONFIG NODE_TOKEN" >&2
  exit 1
fi
role=$1
[[ "$role" == control || "$role" == agent ]] || { echo 'Invalid role' >&2; exit 1; }
binary=$(realpath "$2")
config=$(realpath "$3")
# Complete preflight before changing the installed program or configuration.
if [[ "$role" == control ]]; then
  [[ $# -eq 5 ]] || { echo 'Control requires admin and node token files' >&2; exit 1; }
else
  [[ $# -eq 4 ]] || { echo 'Agent requires one node token file' >&2; exit 1; }
  command -v runc >/dev/null || { echo 'Install runc before installing the agent' >&2; exit 1; }
  command -v python3 >/dev/null || { echo 'Python 3 required to read the agent configuration.' >&2; exit 1; }
  mode=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("cgroupMode") or "auto")' "$config")
  "$binary" check-cgroups -mode "$mode"
fi
install -d -m 0700 /etc/rcs
install -m 0755 "$binary" /usr/local/bin/rcs
if [[ "$role" == control ]]; then
  install -d -m 0700 /var/lib/rcs-control
  install -m 0600 "$config" /etc/rcs/controller.json
  install -m 0600 "$4" /etc/rcs/admin.token
  install -m 0600 "$5" /etc/rcs/node.token
else
  install -d -m 0700 /var/lib/rcs-agent
  install -m 0600 "$config" /etc/rcs/agent.json
  install -m 0600 "$4" /etc/rcs/node.token
fi
install -m 0644 "$base/deploy/rcs-$role.service" "/etc/systemd/system/rcs-$role.service"
systemctl daemon-reload
systemctl enable --now "rcs-$role"
systemctl --no-pager status "rcs-$role"
