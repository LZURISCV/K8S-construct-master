#!/usr/bin/env bash
# Explicit sequential daemon switches; no rollback of runtime data is attempted.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'STOP: use sudo' >&2; exit 1; }
[[ $(uname -m) == riscv64 ]] || { echo 'STOP: riscv64 required' >&2; exit 1; }
version=${1:?Usage: switch.sh 2.0.13|2.1.5}
case "$version" in
  2.0.13) expected='1.6.'; config=/etc/containerd/config-rv-2.0.toml; current_binary=/usr/bin/containerd ;;
  2.1.5) expected='2.0.13'; config=/etc/containerd/config-rv-migration.toml; current_binary=/opt/rv-platform/containerd/2.0.13/bin/containerd ;;
  *) echo 'STOP: only 2.0.13 and 2.1.5 are supported' >&2; exit 1 ;;
esac
[[ -f /var/lib/rv-containerd-upgrade/prepared.sha256 ]] || { echo 'STOP: run prepare.sh first' >&2; exit 1; }
exec 9>/var/lib/rv-containerd-upgrade/lock
flock -n 9 || { echo 'STOP: another upgrade command is running' >&2; exit 1; }
sha256sum -c /var/lib/rv-containerd-upgrade/prepared.sha256
[[ $(systemctl show containerd -p ActiveState --value) == active ]]
[[ $(systemctl show containerd -p KillMode --value) == process ]]
[[ $(systemctl show containerd -p Delegate --value) == yes ]]
main_pid=$(systemctl show containerd -p MainPID --value)
[[ $(readlink -f "/proc/$main_pid/exe") == $(readlink -f "$current_binary") ]] || { echo 'STOP: unexpected running binary/upgrade order' >&2; exit 1; }
new_bin=/opt/rv-platform/containerd/$version/bin
current_version=$(/usr/bin/timeout 15 "$new_bin/ctr" --address /run/containerd/containerd.sock version)
printf '%s\n' "$current_version" | python3 -c '
import re, sys
text=sys.stdin.read()
match=re.search(r"(?ms)^Server:\s*\n.*?^\s*Version:\s*v?(\S+)", text)
expected=sys.argv[1]
ok=bool(match) and (match[1].startswith(expected) if expected.endswith(".") else match[1]==expected)
if not ok: raise SystemExit("STOP: wrong current server version; sequential upgrade required.")
' "$expected"
if [[ $version == 2.0.13 && -e /etc/systemd/system/containerd.service.d/30-rv-version.conf ]]; then
  echo 'STOP: an unexpected override exists' >&2; exit 1
fi
echo "Switching to $version. Kubelet and containerd will restart. Keep this node cordoned until all installation checks pass."
systemctl stop kubelet.service
trap 'echo "STOP: switch did not finish. Keep the node cordoned; inspect journalctl -u containerd -u kubelet. Do not blindly downgrade runtime data." >&2' ERR
install -d -m 0755 /etc/systemd/system/containerd.service.d
unit_temp=$(mktemp /etc/systemd/system/containerd.service.d/.rv-version.XXXXXXXX)
cat > "$unit_temp" <<EOF
[Service]
Environment="PATH=$new_bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=
ExecStart=$new_bin/containerd --config $config
EOF
chmod 0644 "$unit_temp"
mv -f -- "$unit_temp" /etc/systemd/system/containerd.service.d/30-rv-version.conf
systemctl daemon-reload
systemctl restart containerd.service
ready=0
for attempt in $(seq 1 30); do
  if /usr/bin/timeout 3 "$new_bin/ctr" --address /run/containerd/containerd.sock version > /var/lib/rv-containerd-upgrade/current-version.txt 2>/dev/null &&
    /usr/bin/timeout 3 "$new_bin/ctr" --address /run/containerd/containerd.sock plugins ls > /var/lib/rv-containerd-upgrade/current-plugins.txt 2>/dev/null; then
    if python3 - "$version" <<'PY'
from pathlib import Path
import re, sys
version = Path('/var/lib/rv-containerd-upgrade/current-version.txt').read_text()
match = re.search(r'(?ms)^Server:\s*\n.*?^\s*Version:\s*v?(\S+)', version)
plugins = {tuple(parts[:2]): parts[-1] for line in Path('/var/lib/rv-containerd-upgrade/current-plugins.txt').read_text().splitlines() if len(parts := line.split()) >= 4}
required = [('io.containerd.cri.v1', 'images'), ('io.containerd.cri.v1', 'runtime'), ('io.containerd.grpc.v1', 'cri')]
raise SystemExit(not (match and match[1] == sys.argv[1] and all(plugins.get(k) == 'ok' for k in required)))
PY
    then ready=1; break; fi
  fi
  sleep 2
done
[[ $ready == 1 ]] || { echo 'STOP: server version/CRI plugins did not become ready' >&2; false; }
systemctl start kubelet.service
systemctl is-active --quiet kubelet.service
trap - ERR
cat /var/lib/rv-containerd-upgrade/current-version.txt
echo "SWITCHED to $version. Verify node Ready and its runtime version from k8smaster before continuing."
