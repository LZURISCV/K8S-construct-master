#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 root' >&2; exit 1; }
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
task_config=${1:?用法: bash install-controller.sh /绝对路径/config.json}
for task_tool in kubectl python3 ssh; do command -v "$task_tool" >/dev/null; done
kubectl apply -f "$task_dir/manifests/crd.yaml"
kubectl wait --for=condition=Established crd/nodemigrations.migration.riscv.io --timeout=60s
kubectl apply -f "$task_dir/manifests/rbac.yaml"
install -d -m 0700 /etc/rv-platform
install -d -m 0755 /opt/rv-platform/migration
install -m 0755 "$task_dir/controller.py" /opt/rv-platform/migration/controller.py
if [[ $(readlink -f "$task_config") != /etc/rv-platform/migration.json ]]; then
  install -m 0600 "$task_config" /etc/rv-platform/migration.json
fi
umask 077
task_temp=$(mktemp -d)
trap 'rm -f "$task_temp/cluster.json" "$task_temp/secret.json"; rmdir "$task_temp"' EXIT
kubectl config view --raw --minify -o json > "$task_temp/cluster.json"
for task_attempt in $(seq 1 30); do
  kubectl get secret rv-migration-controller-token -n rv-platform -o json > "$task_temp/secret.json"
  if python3 -c 'import json,sys; sys.exit(not bool(json.load(open(sys.argv[1])).get("data",{}).get("token")))' "$task_temp/secret.json"; then break; fi
  sleep 1
done
python3 - "$task_temp/cluster.json" "$task_temp/secret.json" <<'PY'
import base64, json, pathlib, sys
cluster = json.load(open(sys.argv[1]))['clusters'][0]['cluster']
if 'certificate-authority' in cluster:
    cluster['certificate-authority-data'] = base64.b64encode(pathlib.Path(cluster.pop('certificate-authority')).read_bytes()).decode()
secret = json.load(open(sys.argv[2]))
token = base64.b64decode(secret['data']['token']).decode()
out = {'apiVersion':'v1','kind':'Config','clusters':[{'name':'cluster','cluster':cluster}],
       'users':[{'name':'rv-migration','user':{'token':token}}],
       'contexts':[{'name':'default','context':{'cluster':'cluster','user':'rv-migration'}}], 'current-context':'default'}
p = pathlib.Path('/etc/rv-platform/migration.kubeconfig')
p.write_text(json.dumps(out, indent=2)); p.chmod(0o600)
PY
cat > /etc/systemd/system/rv-migration-controller.service <<'EOF'
[Unit]
Description=RISC-V NodeMigration controller
Wants=network-online.target
After=network-online.target kube-apiserver.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/rv-platform/migration/controller.py --config /etc/rv-platform/migration.json
Restart=always
RestartSec=5
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now rv-migration-controller
echo '迁移控制器已安装。使用 systemctl status rv-migration-controller 查看状态。'
