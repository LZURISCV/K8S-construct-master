#!/usr/bin/env bash
# Run on the existing control host. Only node credentials leave this machine.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 root' >&2; exit 1; }
task_node=${1:?节点名}
task_ip=${2:?节点IPv4}
task_server=${3:?API地址，例如 https://192.168.50.70:6443}
task_user=${4:-root}
[[ $task_node =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || { echo '节点名无效' >&2; exit 1; }
[[ $task_user =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]] || { echo 'SSH 用户无效' >&2; exit 1; }
python3 - "$task_ip" "$task_server" <<'PY'
import ipaddress, sys, urllib.parse
assert ipaddress.ip_address(sys.argv[1]).version == 4, '当前辅助脚本需要 IPv4 地址'
u = urllib.parse.urlparse(sys.argv[2])
assert u.scheme == 'https' and u.hostname and not u.username and not u.query and not u.fragment, 'API 地址必须为 HTTPS'
PY
task_ca=/etc/kubernetes/pki
for task_file in ca.pem ca-key.pem kube-proxy.pem kube-proxy-key.pem; do [[ -f "$task_ca/$task_file" ]]; done
for task_tool in openssl kubectl ssh scp python3; do command -v "$task_tool" >/dev/null; done
umask 077
task_dir=$(mktemp -d)
trap 'rm -f "$task_dir"/*; rmdir "$task_dir"' EXIT
openssl req -new -newkey rsa:2048 -nodes -keyout "$task_dir/$task_node-key.pem" \
  -out "$task_dir/node.csr" -subj "/CN=system:node:$task_node/O=system:nodes"
cat > "$task_dir/extensions.cnf" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=clientAuth,serverAuth
subjectAltName=DNS:$task_node,IP:$task_ip
EOF
task_serial=$(openssl rand -hex 16)
openssl x509 -req -in "$task_dir/node.csr" -CA "$task_ca/ca.pem" -CAkey "$task_ca/ca-key.pem" \
  -set_serial "0x$task_serial" -days 365 -sha256 -extfile "$task_dir/extensions.cnf" -out "$task_dir/$task_node.pem"
for task_identity in "$task_node" kube-proxy; do
  task_config="$task_dir/$task_identity.kubeconfig"
  if [[ $task_identity == kube-proxy ]]; then
    task_cert="$task_ca/kube-proxy.pem"; task_key="$task_ca/kube-proxy-key.pem"; task_cn=system:kube-proxy
  else
    task_cert="$task_dir/$task_node.pem"; task_key="$task_dir/$task_node-key.pem"; task_cn="system:node:$task_node"
  fi
  kubectl config set-cluster existing --server="$task_server" --certificate-authority="$task_ca/ca.pem" --embed-certs=true --kubeconfig="$task_config"
  kubectl config set-credentials "$task_cn" --client-certificate="$task_cert" --client-key="$task_key" --embed-certs=true --kubeconfig="$task_config"
  kubectl config set-context default --cluster=existing --user="$task_cn" --kubeconfig="$task_config"
  kubectl config use-context default --kubeconfig="$task_config"
done
ssh -o StrictHostKeyChecking=yes "$task_user@$task_ip" 'install -d -m 0700 /etc/kubernetes/pki'
scp -o StrictHostKeyChecking=yes "$task_ca/ca.pem" "$task_dir/$task_node.pem" "$task_dir/$task_node-key.pem" \
  "$task_dir/$task_node.kubeconfig" "$task_dir/kube-proxy.kubeconfig" "$task_user@$task_ip:/etc/kubernetes/pki/"
ssh -o StrictHostKeyChecking=yes "$task_user@$task_ip" 'chmod 0600 /etc/kubernetes/pki/*-key.pem /etc/kubernetes/pki/*.kubeconfig'
echo "已为 $task_node 分发所需节点证书；控制面的 CA 私钥、管理员凭据不分发。"
