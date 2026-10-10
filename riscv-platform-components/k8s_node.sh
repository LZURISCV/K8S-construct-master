#!/bin/bash
set -euo pipefail

#检查是否为root
if [ "$EUID" -ne 0 ]; then
  echo "请使用 root 用户运行此脚本"
  exit 1
fi

# 获取当前主机 IP
read -p "请输入当前主机ip: " MY_IP
echo "当前主机 IP 检测为: $MY_IP"

# 获取当前主机名（或让用户手动输入）
read -p "请输入当前主机名（例如 k8smaster 或 k8snode1）: " MY_HOSTNAME
hostnamectl set-hostname "$MY_HOSTNAME"

# 添加自己到 /etc/hosts
echo "$MY_IP $MY_HOSTNAME" >> /etc/hosts

# 让用户输入其他节点信息
read -p "请输入其他节点（格式 IP:主机名，用空格分隔，例如 192.168.50.70:k8snode1）: " OTHER_NODES

# 添加其他节点到 /etc/hosts
for entry in $OTHER_NODES; do
  echo "$entry" | awk -F':' '{ print $1, $2 }' >> /etc/hosts
done

# 关闭 swap
swapoff -a
sed -i '/swap/d' /etc/fstab

# 关闭 SELinux
if command -v getenforce >/dev/null && [[ $(getenforce) == Enforcing ]]; then
  setenforce 0
fi
if [[ -f /etc/selinux/config ]]; then
  sed -i 's/^SELINUX=enforcing/SELINUX=disabled/' /etc/selinux/config
fi

# 安装必要软件
dnf install -y docker conntrack-tools socat containernetworking-plugins

# 配置 CNI 插件
mkdir -p /opt/cni/bin
cp /usr/libexec/cni/* /opt/cni/bin 2>/dev/null || echo "⚠️  未找到 /usr/libexec/cni 插件，跳过拷贝"

# 加载模块并配置开机加载
modprobe br_netfilter
modprobe vxlan
cat > /etc/modules-load.d/k8s.conf <<EOF
br_netfilter
vxlan
EOF
cat > /etc/sysctl.d/90-k8s-network.conf <<'EOF'
net.ipv4.ip_forward = 1
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
EOF
sysctl --system
if systemctl is-active --quiet firewalld; then
  firewall-cmd --zone=public --add-port=10250/tcp --permanent
  firewall-cmd --zone=public --add-port=8472/udp --permanent
  firewall-cmd --reload
fi

# 安装并配置 containerd
dnf install -y containerd
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml
sed -i 's#sandbox_image = .*#sandbox_image = "cyber1010/k8s:pause-riscv64-3.6"#' /etc/containerd/config.toml
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml

systemctl daemon-reexec
systemctl daemon-reload
systemctl enable --now containerd

echo "✅ 初始化完成，建议执行 reboot 进行重启"



# 工作节点连接既有 API Server；etcd 由控制面管理，不在每个工作节点另建集群。

#下载node组件
dnf install -y kubernetes-node

#获取node 节点的 IP 和用户名
NODE_IP="$MY_IP"
NODE_NAME="$MY_HOSTNAME"

echo "[INFO] 开始配置 kubelet 和 kube-proxy..."

mkdir -p /etc/kubernetes/pki

# 写入 kubelet 配置文件
cat > /etc/kubernetes/pki/kubelet_config.yaml <<EOF
kind: KubeletConfiguration
apiVersion: kubelet.config.k8s.io/v1beta1
cgroupDriver: systemd
authentication:
  anonymous:
    enabled: false
  webhook:
    enabled: true
  x509:
    clientCAFile: /etc/kubernetes/pki/ca.pem
authorization:
  mode: Webhook
clusterDNS:
  - 10.32.0.10
clusterDomain: cluster.local
runtimeRequestTimeout: "15m"
tlsCertFile: "/etc/kubernetes/pki/${NODE_NAME}.pem"
tlsPrivateKeyFile: "/etc/kubernetes/pki/${NODE_NAME}-key.pem"
EOF

# 新版 kubelet 默认拒绝 cgroup v1；仅向支持此字段的版本写入显式配置。
KUBELET_MINOR=$(kubelet --version | sed -n 's/.*v1\.\([0-9][0-9]*\).*/\1/p')
if [[ -n "$KUBELET_MINOR" && "$KUBELET_MINOR" -ge 35 && ! -f /sys/fs/cgroup/cgroup.controllers ]]; then
  echo 'failCgroupV1: false' >> /etc/kubernetes/pki/kubelet_config.yaml
fi

# 写入 kubelet 启动参数配置
cat > /etc/kubernetes/kubelet.conf <<EOF
KUBELET_HOSTNAME="--hostname-override=${NODE_NAME}"
KUBELET_ADDRESS="--address=${NODE_IP}"
KUBELET_CONFIG="--config=/etc/kubernetes/pki/kubelet_config.yaml"
KUBELET_KUBECONFIG="--kubeconfig=/etc/kubernetes/pki/${NODE_NAME}.kubeconfig"
KUBELET_ARGS="--fail-swap-on=false --register-node=true --container-runtime-endpoint=unix:///run/containerd/containerd.sock"
EOF

# 创建 kubelet systemd 服务文件
cat > /usr/lib/systemd/system/kubelet.service <<EOF
[Unit]
Description=kubelet: The Kubernetes Node Agent
Documentation=https://kubernetes.io/docs/
Wants=network-online.target
After=network-online.target

[Service]
EnvironmentFile=-/etc/kubernetes/kubelet.conf
ExecStart=/usr/bin/kubelet \\
  \$KUBELET_HOSTNAME \\
  \$KUBELET_ADDRESS \\
  \$KUBELET_CONFIG \\
  \$KUBELET_KUBECONFIG \\
  \$KUBELET_ARGS
Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# 写入 kube-proxy 配置文件
cat > /etc/kubernetes/pki/kube_proxy_config.yaml <<EOF
kind: KubeProxyConfiguration
apiVersion: kubeproxy.config.k8s.io/v1alpha1
clientConnection:
  kubeconfig: /etc/kubernetes/pki/kube-proxy.kubeconfig
clusterCIDR: 10.244.0.0/16
mode: "iptables"
EOF

# 写入 kube-proxy 启动参数
cat > /etc/kubernetes/proxy.conf <<EOF
KUBE_CONFIG="--config=/etc/kubernetes/pki/kube_proxy_config.yaml"
KUBE_HOSTNAME="--hostname-override=${NODE_NAME}"
KUBE_PROXY_ARGS="--v=2"
EOF

# 创建 kube-proxy systemd 服务文件
cat > /usr/lib/systemd/system/kube-proxy.service <<EOF
[Unit]
Description=Kubernetes Kube-Proxy Server
Documentation=https://kubernetes.io/docs/reference/generated/kube-proxy/
After=network.target

[Service]
EnvironmentFile=/etc/kubernetes/proxy.conf
ExecStart=/usr/bin/kube-proxy \\
  \$KUBE_CONFIG \\
  \$KUBE_HOSTNAME \\
  \$KUBE_PROXY_ARGS
Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# 启动并设置开机自启
echo "[INFO] 启动 kubelet 和 kube-proxy..."
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable kubelet kube-proxy
systemctl restart kubelet kube-proxy

echo "[DONE] Node 节点组件已配置完成！"
