#!/usr/bin/env bash
# Install the cache-first 2.1.5 build and updated helper after the node is cordoned.
set -euo pipefail
[[ $EUID -eq 0 && $(uname -m) == riscv64 ]] || { echo '请在 RISC-V 工作端使用 sudo 执行。' >&2; exit 1; }
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
task_old=/opt/rv-platform/containerd/2.1.5/bin
task_new=/opt/rv-platform/containerd/2.1.5-rv1/bin
task_config=/etc/containerd/config-rv-migration.toml
for tool in python3 flock systemctl sha256sum install timeout cmp; do command -v "$tool" >/dev/null; done
[[ -f "$task_config" && -x "$task_old/ctr" ]]
install -d -m 0700 /var/lib/rv-migration
exec 9>/var/lib/rv-migration/runtime-update.lock
flock -n 9 || { echo '另一更新命令正在运行。' >&2; exit 1; }
for unit in containerd kubelet rv-migration-proxy; do systemctl is-active --quiet "$unit"; done
[[ $(systemctl show containerd -p KillMode --value) == process ]]
[[ $(systemctl show containerd -p Delegate --value) == yes ]]
(cd "$task_dir/offline-runtime/bin"; sha256sum -c SHA256SUMS)
(cd "$task_dir/bin"; sha256sum -c SHA256SUMS)
"$task_old/ctr" --address /run/containerd/containerd.sock version | python3 -c '
import re,sys
m=re.search(r"(?ms)^Server:\s*\n.*?^\s*Version:\s*v?(\S+)",sys.stdin.read())
if not m or m[1] not in ("2.1.5", "2.1.5-rv1"): raise SystemExit("需要先完成 containerd 2.1.5 和迁移代理安装。")
'
task_pid=$(systemctl show containerd -p MainPID --value)
task_running=$(readlink -f "/proc/$task_pid/exe")
[[ "$task_running" == "$task_old/containerd" || "$task_running" == "$task_new/containerd" ]] || { echo '实际 containerd 路径不符；未修改服务。' >&2; exit 1; }
# Keep the original stock binaries and the prepared upgrade manifest intact.
printf '%s\n' \
  '08b133d82fb49bc57bc5b530328f2b8f84b677d18ce6e4b88667ad7bdc9be8bf  containerd-shim-runc-v2' \
  'e55c15a4a8b6624fe0c4a256c83002e0b1d4341e1cc3e41f255d286d8b0ec5a4  ctr' \
  | (cd "$task_old"; sha256sum -c -)
"$task_dir/offline-runtime/bin/containerd" --version
"$task_dir/bin/rv-migrate-runtime" probe
# A version directory is immutable. Re-running the same installer must not
# overwrite a daemon or shim executable that is already mapped by a process.
for binary in containerd containerd-shim-runc-v2 ctr; do
  if [[ "$binary" == containerd ]]; then
    task_from="$task_dir/offline-runtime/bin/$binary"
  else
    task_from="$task_old/$binary"
  fi
  if [[ -e "$task_new/$binary" ]] && ! cmp -s "$task_from" "$task_new/$binary"; then
    echo "版本目录已有不同的 $binary，未停止服务；请核对补充包。" >&2
    exit 1
  fi
done
task_backup=$(mktemp -d /var/lib/rv-migration/runtime-backup.XXXXXXXX)
cp -p /etc/systemd/system/containerd.service.d/30-rv-version.conf "$task_backup/30-rv-version.conf"
cp -p /usr/local/libexec/rv-migrate-runtime "$task_backup/rv-migrate-runtime"
cp -p /etc/kubernetes/kubelet.conf "$task_backup/kubelet.conf"
echo "本次安装备份：$task_backup"
trap 'echo "更新未完成。保持节点 cordon，查看 containerd、rv-migration-proxy、kubelet 日志；不要启动恢复测试。" >&2' ERR
systemctl stop kubelet
systemctl stop rv-migration-proxy
install -d -m 0755 "$task_new"
[[ -e "$task_new/containerd" ]] || install -m 0755 "$task_dir/offline-runtime/bin/containerd" "$task_new/containerd"
for binary in containerd-shim-runc-v2 ctr; do
  [[ -e "$task_new/$binary" ]] || install -m 0755 "$task_old/$binary" "$task_new/$binary"
done
install -m 0755 "$task_dir/bin/rv-migrate-runtime" /usr/local/libexec/rv-migrate-runtime
cat > /etc/systemd/system/containerd.service.d/30-rv-version.conf <<EOF
[Service]
Environment="PATH=$task_new:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=
ExecStart=$task_new/containerd --config $task_config
EOF
systemctl daemon-reload
systemctl restart containerd
task_ready=0
for attempt in $(seq 1 30); do
  if timeout 3 "$task_new/ctr" version > "$task_backup/new-version.txt" 2>/dev/null &&
     timeout 3 "$task_new/ctr" plugins ls > "$task_backup/new-plugins.txt" 2>/dev/null; then
    if python3 - "$task_backup" <<'PY'
from pathlib import Path
import re, sys
p = Path(sys.argv[1])
m = re.search(r'(?ms)^Server:\s*\n.*?^\s*Version:\s*v?(\S+)', (p/'new-version.txt').read_text())
plugins = {tuple(v[:2]): v[-1] for line in (p/'new-plugins.txt').read_text().splitlines() if len(v := line.split()) >= 4}
required = [('io.containerd.cri.v1','images'), ('io.containerd.cri.v1','runtime'), ('io.containerd.grpc.v1','cri')]
raise SystemExit(not (m and m[1] == '2.1.5-rv1' and all(plugins.get(k) == 'ok' for k in required)))
PY
    then task_ready=1; break; fi
  fi
  sleep 2
done
[[ $task_ready == 1 ]] || { echo '新 containerd/CRI 未就绪。' >&2; false; }
systemctl restart rv-migration-proxy
task_proxy=0
for attempt in $(seq 1 30); do
  if timeout 5 /usr/local/libexec/rv-migrate-runtime proxy-ready; then task_proxy=1; break; fi
  sleep 1
done
[[ $task_proxy == 1 ]]
/usr/local/libexec/rv-migrate-runtime preflight
systemctl start kubelet
systemctl is-active --quiet kubelet
trap - ERR
echo "工作端更新完成。备份位于 $task_backup；请回控制端确认节点 Ready 后 uncordon。"
