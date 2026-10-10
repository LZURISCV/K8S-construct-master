#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '使用 root'; exit 1; }
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
command -v python3 >/dev/null
install -d -m 0755 /usr/local/libexec
install -m 0755 "$task_dir/agent.py" /usr/local/libexec/rv-node
cat > /etc/systemd/system/rv-node-exporter.service <<'EOF'
[Unit]
Description=RISC-V platform Linux HAL metrics
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/libexec/rv-node exporter --listen 0.0.0.0 --port 9108
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now rv-node-exporter
echo '节点模块已安装；仅向控制机/Prometheus 放行 TCP 9108。'

