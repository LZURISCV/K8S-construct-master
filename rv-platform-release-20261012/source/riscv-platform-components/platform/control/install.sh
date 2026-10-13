#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 && $# -eq 1 ]] || { echo 'root: install.sh /绝对路径/platform.json'; exit 1; }
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
task_config=$(readlink -f "$1")
for task_tool in kubectl python3; do command -v "$task_tool" >/dev/null; done
python3 - "$task_config" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))
assert c.get('modules') and set(c['modules']) <= {'resource','network-host','scenario'}
if 'scenario' in c['modules']: assert c.get('prometheusURL','').startswith(('http://','https://'))
assert c.get('kubeconfig') == '/etc/rv-platform/platform.kubeconfig'
PY
if kubectl get crd directworkloads.platform.riscv.io >/dev/null 2>&1; then
  task_old_count=$(kubectl get directworkloads.platform.riscv.io -A -o json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["items"]))')
  if [[ "$task_old_count" -ne 0 ]]; then
    echo '旧直通任务尚未回收。请先用仍运行的旧控制器完成手册 6.5 的正常回收，再安装新控制器。' >&2
    exit 1
  fi
fi
python3 "$task_dir/manifests.py" | kubectl apply -f -
for task_crd in resourceclaims resourcepolicies nodeprofiles hostnetworkworkloads scenariopolicies; do
  kubectl wait --for=condition=Established "crd/$task_crd.platform.riscv.io" --timeout=60s
done
install -d -m 0700 /etc/rv-platform
install -d -m 0755 /opt/rv-platform/control
install -m 0644 "$task_dir/"*.py /opt/rv-platform/control/
umask 077
if [[ "$task_config" != /etc/rv-platform/platform.json ]]; then
  install -m 0600 "$task_config" /etc/rv-platform/platform.json
fi
python3 "$task_dir/credentials.py" rv-platform-controller /etc/rv-platform/platform.kubeconfig
cat > /etc/systemd/system/rv-platform-controller.service <<'EOF'
[Unit]
Description=RISC-V resource, host TCP network and scenario controllers
After=network-online.target kube-apiserver.service
Wants=network-online.target
[Service]
ExecStart=/usr/bin/python3 /opt/rv-platform/control/controller.py --config /etc/rv-platform/platform.json
Restart=always
RestartSec=5
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable rv-platform-controller
systemctl restart rv-platform-controller
echo '控制组件已安装。迁移仍使用独立 rv-migration-controller 服务。'

