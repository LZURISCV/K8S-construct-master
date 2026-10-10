#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 && $# -eq 1 ]] || { echo 'root: install.sh /绝对路径/devices.json'; exit 1; }
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
source "$task_dir/../scripts/go-env.sh"
task_build=$(mktemp)
trap 'rm -f "$task_build"' EXIT
(cd "$task_dir"; CGO_ENABLED=0 GOOS=linux GOARCH=riscv64 go build -mod=readonly -trimpath -o "$task_build" .)
install -d -m 0700 /etc/rv-platform
install -d -m 0755 /usr/local/libexec
install -m 0755 "$task_build" /usr/local/libexec/rv-device-plugin
task_config=$(readlink -f "$1")
if [[ "$task_config" != /etc/rv-platform/devices.json ]]; then install -m 0600 "$task_config" /etc/rv-platform/devices.json; fi
cat > /etc/systemd/system/rv-device-plugin.service <<'EOF'
[Unit]
Description=RISC-V static hardware device plugin
After=kubelet.service
Requires=kubelet.service
[Service]
ExecStart=/usr/local/libexec/rv-device-plugin --config /etc/rv-platform/devices.json
Restart=always
RestartSec=5
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable rv-device-plugin
systemctl restart rv-device-plugin
echo '查看 Node allocatable 中 hardware.riscv.io/资源名。Unhealthy 不会强行终止已分配容器。'

