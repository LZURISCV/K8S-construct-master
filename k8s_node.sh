#!/bin/bash

#检查是否为root
if [ "$EUID" -ne 0 ]; then
  echo "请使用 root 用户运行此脚本"
  exit 1
fi

# 根据当前 Worker 服务器固定 IP
MY_IP="192.168.102.125"
echo "当前主机 IP 检测为: $MY_IP"

# 固定 Worker 主机名
MY_HOSTNAME="k8snode1"
hostnamectl set-hostname "$MY_HOSTNAME"

# 添加自己到 /etc/hosts
echo "$MY_IP $MY_HOSTNAME" >> /etc/hosts

# Master 节点信息
OTHER_NODES="192.168.102.130:k8smaster"

# 添加其他节点到 /etc/hosts
for entry in $OTHER_NODES; do
  echo "$entry" | awk -F':' '{ print $1, $2 }' >> /etc/hosts
done

# 关闭 swap
swapoff -a
sed -i '/swap/d' /etc/fstab

# 关闭 SELinux
setenforce 0
sed -i 's/^SELINUX=enforcing/SELINUX=disabled/' /etc/selinux/config

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

# 安装并配置 containerd
dnf install -y containerd
containerd config default > /etc/containerd/config.toml
sed -i 's#sandbox_image = .*#sandbox_image = "cyber1010/k8s:pause-riscv64-3.6"#' /etc/containerd/config.toml
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml

systemctl daemon-reexec
systemctl daemon-reload
systemctl enable --now containerd

echo "✅ 初始化完成，建议执行 reboot 进行重启"



ETCD_NAME=$(hostname)  # 使用主机名作为 etcd 名字

echo "当前主机 IP 为: $MY_IP"
echo "使用 etcd 名字: $ETCD_NAME"

# 安装 etcd
sudo dnf install -y etcd

# 开启 etcd 所需端口
sudo firewall-cmd --zone=public --add-port=2379/tcp --permanent
sudo firewall-cmd --zone=public --add-port=2380/tcp --permanent
sudo firewall-cmd --zone=public --add-port=443/tcp --permanent
sudo firewall-cmd --zone=public --add-port=4443/tcp --permanent
sudo firewall-cmd --reload

# 创建所需目录
sudo mkdir -p /etc/etcd /var/lib/etcd

# 创建 etcd.conf
cat <<EOF | sudo tee /etc/etcd/etcd.conf
ETCD_NAME=$ETCD_NAME
ETCD_ENABLE_V2="true"
EOF

# 生成 systemd service 文件
cat <<EOF | sudo tee /usr/lib/systemd/system/etcd.service
[Unit]
Description=Etcd Server
After=network.target
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/var/lib/etcd/
EnvironmentFile=/etc/etcd/etcd.conf
ExecStart=/usr/bin/etcd \\
  --cert-file=/etc/etcd/etcd.pem \\
  --key-file=/etc/etcd/etcd-key.pem \\
  --peer-cert-file=/etc/etcd/etcd.pem \\
  --peer-key-file=/etc/etcd/etcd-key.pem \\
  --trusted-ca-file=/etc/etcd/ca.pem \\
  --peer-trusted-ca-file=/etc/etcd/ca.pem \\
  --peer-client-cert-auth \\
  --client-cert-auth \\
  --initial-advertise-peer-urls https://$MY_IP:2380 \\
  --listen-peer-urls https://$MY_IP:2380 \\
  --listen-client-urls https://$MY_IP:2379,https://127.0.0.1:2379 \\
  --advertise-client-urls https://$MY_IP:2379 \\
  --initial-cluster-token etcd-cluster-0 \\
  --initial-cluster $ETCD_NAME=https://$MY_IP:2380 \\
  --initial-cluster-state new \\
  --data-dir /var/lib/etcd
Restart=always
RestartSec=10s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# 启动 etcd
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable etcd
sudo systemctl start etcd

echo "✅ etcd 安装和配置完成，已启动！"

#下载node组件
dnf install kubernetes-node

# Worker 节点 IP 和在 Kubernetes 中的节点名
NODE_IP="192.168.102.125"
NODE_NAME="k8snode1"
echo "Worker: $NODE_IP $NODE_NAME"

echo "[INFO] 开始配置 kubelet 和 kube-proxy..."

mkdir -p /etc/kubernetes/pki

# 写入 kubelet 配置文件
cat > /etc/kubernetes/pki/kubelet_config.yaml <<EOF
kind: KubeletConfiguration
apiVersion: kubelet.config.k8s.io/v1beta1
authentication:
  anonymous:
    enabled: true
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

# 写入 kubelet 启动参数配置
cat > /etc/kubernetes/kubelet.conf <<EOF
KUBELET_HOSTNAME="--hostname-override=${NODE_NAME}"
KUBELET_ADDRESS="--address=${NODE_IP}"
KUBELET_CONFIG="--config=/etc/kubernetes/pki/kubelet_config.yaml"
KUBELET_KUBECONFIG="--kubeconfig=/etc/kubernetes/pki/${NODE_NAME}.kubeconfig"
KUBELET_ARGS="--cgroup-driver=systemd --fail-swap-on=false --register-node=true --container-runtime-endpoint=unix:///run/containerd/containerd.sock"
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
