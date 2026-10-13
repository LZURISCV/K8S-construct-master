#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 root' >&2; exit 1; }
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ -x "$task_dir/bin/rv-migrate-runtime" ]] || { echo '请先运行 scripts/build.sh' >&2; exit 1; }
for task_tool in containerd runc criu python3 systemctl; do command -v "$task_tool" >/dev/null; done
[[ -f /etc/kubernetes/kubelet.conf ]] || { echo '没有找到现有脚本的 kubelet.conf' >&2; exit 1; }
"$task_dir/bin/rv-migrate-runtime" probe
install -d -m 0700 /var/lib/rv-migration
install -d -m 0755 /usr/local/libexec /etc/systemd/system/kubelet.service.d
install -m 0755 "$task_dir/bin/rv-migrate-runtime" /usr/local/libexec/rv-migrate-runtime
cat > /etc/systemd/system/rv-migration-proxy.service <<'EOF'
[Unit]
Description=RISC-V container migration CRI proxy
Requires=containerd.service
After=containerd.service
Before=kubelet.service
[Service]
Type=simple
ExecStart=/usr/local/libexec/rv-migrate-runtime proxy
Restart=always
RestartSec=1
RuntimeDirectory=rv-migration
RuntimeDirectoryMode=0700
UMask=0077
TimeoutStopSec=15
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/kubelet.service.d/30-rv-migration.conf <<'EOF'
[Unit]
Requires=rv-migration-proxy.service
After=rv-migration-proxy.service
EOF
if [[ ! -f /etc/kubernetes/kubelet.conf.before-rv-migration ]]; then
  cp -p /etc/kubernetes/kubelet.conf /etc/kubernetes/kubelet.conf.before-rv-migration
fi
# Update only runtime/image endpoints in this project's existing EnvironmentFile.
python3 "$task_dir/configure_kubelet.py" /etc/kubernetes/kubelet.conf
systemctl daemon-reload
systemctl enable --now rv-migration-proxy
# Wait for the actual CRI Version RPC before restarting kubelet.
python3 - <<'PY'
import pathlib, time
for _ in range(50):
    if pathlib.Path('/run/rv-migration/cri.sock').exists():
        break
    time.sleep(.1)
else:
    raise SystemExit('CRI 代理启动超时；未重启 kubelet')
PY
/usr/local/libexec/rv-migrate-runtime proxy-ready
systemctl restart kubelet
echo '节点迁移运行时已安装；原 kubelet 配置已备份。'
