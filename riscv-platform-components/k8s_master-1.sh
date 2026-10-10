#!/bin/bash
set -euo pipefail
trap 'echo "❌ 脚本失败，出错行：$LINENO，退出码：$?"' ERR

# 让用户分别输入节点 IP 和主机名
read -p "请输入本节点 IP: " MASTER_IP
read -p "请输入当前主机名（例如 k8smaster 或 k8snode1）: " MY_HOSTNAME
hostnamectl set-hostname "$MY_HOSTNAME"

# 添加自己到 /etc/hosts
echo "$MASTER_IP $MY_HOSTNAME" >> /etc/hosts

# 让用户分别输入节点 IP 和主机名
read -p "请输入其他节点 IP: " NODE_IP
read -p "请输入其他节点主机名: " NODE_HOSTNAME

# 拼接为 IP:主机名 的格式
OTHER_NODES="${MASTER_IP}:${NODE_HOSTNAME}"

# 添加到 /etc/hosts
echo "$NODE_IP $NODE_HOSTNAME" >> /etc/hosts

# 输出确认
echo "你输入的节点信息是: $OTHER_NODES"

echo "⛔ 关闭 swap..."
swapoff -a

echo "������ 关闭 SELinux..."
setenforce 0 || true

echo "������ 安装基础软件..."
dnf install -y docker conntrack-tools socat containernetworking-plugins

echo "������ 拷贝 CNI 插件到 /opt/cni/bin..."
mkdir -p /opt/cni/bin
cp /usr/libexec/cni/* /opt/cni/bin

echo "������ 加载内核模块..."
modprobe br_netfilter
modprobe vxlan
cat > /etc/modules-load.d/k8s.conf <<EOF
br_netfilter
vxlan
EOF

echo "������ 安装 containerd..."
dnf install -y containerd

echo "⚙️ 配置 containerd..."
containerd config default > /etc/containerd/config.toml
sed -i 's#sandbox_image =.*#sandbox_image = "cyber1010/k8s:pause-riscv64-3.6"#' /etc/containerd/config.toml
sed -i 's#SystemdCgroup = false#SystemdCgroup = true#' /etc/containerd/config.toml

echo "������ 启动 containerd..."
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable containerd --now

echo "✅ 初始化完成，建议执行 reboot 进行重启"


# 安装必要的依赖
dnf install -y make git golang tar

# 下载并安装 cfssl
wget https://github.com/cloudflare/cfssl/archive/v1.5.0.tar.gz
tar -zxf v1.5.0.tar.gz
cd cfssl-1.5.0/
make -j6
cp bin/* /usr/local/bin/

# 返回脚本初始目录
cd ..

# 写入 hosts（可选）
cat >> /etc/hosts <<EOF
$MASTER_IP k8smaster
$NODE_IP $NODE_HOSTNAME
EOF

# 创建 CA 配置文件
cat > ca-config.json <<EOF
{
  "signing": {
    "default": {
      "expiry": "8760h"
    },
    "profiles": {
      "kubernetes": {
        "usages": ["signing", "key encipherment", "server auth", "client auth"],
        "expiry": "8760h"
      }
    }
  }
}
EOF

# 创建 CA 请求文件
cat > ca-csr.json <<EOF
{
  "CN": "kubernetes",
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "Kubernetes",
      "OU": "WWW"
    }
  ]
}
EOF

# 生成 CA 证书
cfssl gencert -initca ca-csr.json | cfssljson -bare ca

# 生成 admin 证书
cat > admin-csr.json <<EOF
{
  "CN": "admin",
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "system:masters",
      "OU": "Containerum"
    }
  ]
}
EOF

cfssl gencert \
  -ca=ca.pem -ca-key=ca-key.pem -config=ca-config.json \
  -profile=kubernetes admin-csr.json | cfssljson -bare admin

# 生成 service-account 证书
cat > service-account-csr.json <<EOF
{
  "CN": "service-accounts",
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "Kubernetes",
      "OU": "eulixOS k8s install"
    }
  ]
}
EOF

cfssl gencert \
  -ca=ca.pem -ca-key=ca-key.pem -config=ca-config.json \
  -profile=kubernetes service-account-csr.json | cfssljson -bare service-account

# 生成 kube-controller-manager 证书
cat > kube-controller-manager-csr.json <<EOF
{
  "CN": "system:kube-controller-manager",
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "system:kube-controller-manager",
      "OU": "eulixos k8s kcm"
    }
  ]
}
EOF

cfssl gencert \
  -ca=ca.pem -ca-key=ca-key.pem -config=ca-config.json \
  -profile=kubernetes kube-controller-manager-csr.json | cfssljson -bare kube-controller-manager

# 生成 kube-scheduler 证书
cat > kube-scheduler-csr.json <<EOF
{
  "CN": "system:kube-scheduler",
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "system:kube-scheduler",
      "OU": "eulixos k8s kube scheduler"
    }
  ]
}
EOF

cfssl gencert \
  -ca=ca.pem -ca-key=ca-key.pem -config=ca-config.json \
  -profile=kubernetes kube-scheduler-csr.json | cfssljson -bare kube-scheduler

# 生成 apiserver 证书
cat > apiserver-csr.json <<EOF
{
  "CN": "kube-apiserver",
  "hosts": [
    "10.32.0.1",
    "$MASTER_IP",
    "127.0.0.1",
    "kubernetes",
    "kubernetes.default",
    "kubernetes.default.svc",
    "kubernetes.default.svc.cluster",
    "kubernetes.default.svc.cluster.local"
  ],
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "Kubernetes",
      "OU": "eulixos k8s kube api server"
    }
  ]
}
EOF

cfssl gencert \
  -ca=ca.pem -ca-key=ca-key.pem -config=ca-config.json \
  -profile=kubernetes apiserver-csr.json | cfssljson -bare apiserver

# 生成 front-proxy-ca 证书
cat > front-proxy-ca-csr.json <<EOF
{
  "CN": "front-proxy-ca",
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "ca": {
    "expiry": "876000h"
  }
}
EOF

cfssl gencert -initca front-proxy-ca-csr.json | cfssljson -bare front-proxy-ca

# 生成 front-proxy-client 证书
cat > front-proxy-client-csr.json <<EOF
{
  "CN": "front-proxy-client",
  "hosts": [""],
  "key": {
    "algo": "rsa",
    "size": 2048
  }
}
EOF

cfssl gencert \
  -ca=front-proxy-ca.pem -ca-key=front-proxy-ca-key.pem -config=ca-config.json \
  -profile=kubernetes front-proxy-client-csr.json | cfssljson -bare front-proxy-client

# 生成 etcd 证书
cat > etcd-csr.json <<EOF
{
  "CN": "etcd",
  "hosts": [
    "$MASTER_IP",
    "$NODE_IP",
    "127.0.0.1"
  ],
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "etcd",
      "OU": "eulixos k8s etcd"
    }
  ]
}
EOF

cfssl gencert \
  -ca=ca.pem -ca-key=ca-key.pem -config=ca-config.json \
  -profile=kubernetes etcd-csr.json | cfssljson -bare etcd

# 生成 node kubelet 证书
cat > ${NODE_HOSTNAME}-csr.json <<EOF
{
  "CN": "system:node:${NODE_HOSTNAME}",
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "system:nodes",
      "OU": "openEuler k8s kubelet"
    }
  ]
}
EOF

cfssl gencert \
  -ca=ca.pem -ca-key=ca-key.pem -config=ca-config.json \
  -hostname=${NODE_HOSTNAME},${NODE_IP} -profile=kubernetes ${NODE_HOSTNAME}-csr.json | cfssljson -bare ${NODE_HOSTNAME}

# 生成 kube-proxy 证书
cat > kube-proxy-csr.json <<EOF
{
  "CN": "system:kube-proxy",
  "hosts": [""],
  "key": {
    "algo": "rsa",
    "size": 2048
  },
  "names": [
    {
      "C": "CN",
      "ST": "BinJiang",
      "L": "HangZhou",
      "O": "system:node-proxier",
      "OU": "eulixos k8s kube proxy"
    }
  ]
}
EOF

cfssl gencert \
  -ca=ca.pem -ca-key=ca-key.pem -config=ca-config.json \
  -profile=kubernetes kube-proxy-csr.json | cfssljson -bare kube-proxy

echo "✅ 所有必要的证书已生成完成"


#自动检测当前主机IP（非 127.0.0.1 的第一个）

ETCD_NAME=$(hostname)  # 使用主机名作为 etcd 名字

echo "当前主机 IP 为: $MASTER_IP"
echo "使用 etcd 名字: $ETCD_NAME"

# 安装 etcd
sudo dnf install -y etcd

# 开启 etcd 所需端口
sudo firewall-cmd --zone=public --add-port=2379/tcp --permanent
sudo firewall-cmd --zone=public --add-port=2380/tcp --permanent
sudo firewall-cmd --reload

# 创建所需目录
sudo mkdir -p /etc/etcd /var/lib/etcd

# 拷贝证书（假设在当前目录）
sudo cp ca.pem etcd.pem etcd-key.pem /etc/etcd/

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
  --initial-advertise-peer-urls https://$MASTER_IP:2380 \\
  --listen-peer-urls https://$MASTER_IP:2380 \\
  --listen-client-urls https://$MASTER_IP:2379,https://127.0.0.1:2379 \\
  --advertise-client-urls https://$MASTER_IP:2379 \\
  --initial-cluster-token etcd-cluster-0 \\
  --initial-cluster $ETCD_NAME=https://$MASTER_IP:2380 \\
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




echo "[+] 安装 kubernetes-master 组件"
if ! rpm -q kubernetes-master &>/dev/null; then
    dnf install -y kubernetes-master
else
    echo "[√] kubernetes-master 已安装，跳过安装步骤"
fi

CLUSTER_NAME="eulixos-k8s"
CERT_DIR="/etc/kubernetes/pki"
KUBECONFIG_DIR="/etc/kubernetes/pki"

echo "[+] 创建证书目录并拷贝证书"
mkdir -p $CERT_DIR
cp *.pem $CERT_DIR

echo "[+] 生成 kubeconfig 文件"
# controller-manager
kubectl config set-cluster $CLUSTER_NAME --certificate-authority=$CERT_DIR/ca.pem --embed-certs=true --server=https://$MASTER_IP:6443 --kubeconfig=kube-controller-manager.kubeconfig
kubectl config set-credentials system:kube-controller-manager --client-certificate=$CERT_DIR/kube-controller-manager.pem --client-key=$CERT_DIR/kube-controller-manager-key.pem --embed-certs=true --kubeconfig=kube-controller-manager.kubeconfig
kubectl config set-context default --cluster=$CLUSTER_NAME --user=system:kube-controller-manager --kubeconfig=kube-controller-manager.kubeconfig
kubectl config use-context default --kubeconfig=kube-controller-manager.kubeconfig

# scheduler
kubectl config set-cluster $CLUSTER_NAME --certificate-authority=$CERT_DIR/ca.pem --embed-certs=true --server=https://$MASTER_IP:6443 --kubeconfig=kube-scheduler.kubeconfig
kubectl config set-credentials system:kube-scheduler --client-certificate=$CERT_DIR/kube-scheduler.pem --client-key=$CERT_DIR/kube-scheduler-key.pem --embed-certs=true --kubeconfig=kube-scheduler.kubeconfig
kubectl config set-context default --cluster=$CLUSTER_NAME --user=system:kube-scheduler --kubeconfig=kube-scheduler.kubeconfig
kubectl config use-context default --kubeconfig=kube-scheduler.kubeconfig

# admin
kubectl config set-cluster $CLUSTER_NAME --certificate-authority=$CERT_DIR/ca.pem --embed-certs=true --server=https://$MASTER_IP:6443 --kubeconfig=admin.kubeconfig
kubectl config set-credentials admin --client-certificate=$CERT_DIR/admin.pem --client-key=$CERT_DIR/admin-key.pem --embed-certs=true --kubeconfig=admin.kubeconfig
kubectl config set-context default --cluster=$CLUSTER_NAME --user=admin --kubeconfig=admin.kubeconfig
kubectl config use-context default --kubeconfig=admin.kubeconfig

echo "[+] 移动 kubeconfig 文件到 ${KUBECONFIG_DIR}"
cp *.kubeconfig $KUBECONFIG_DIR

echo "[+] 开放6443端口"
firewall-cmd --zone=public --add-port=6443/tcp --permanent
firewall-cmd --reload

echo "[+] 创建加密配置文件"
bash <<EOF
ENCRYPTION_KEY=\$(head -c 32 /dev/urandom | base64)
cat > $CERT_DIR/encryption-config.yaml <<EOL
kind: EncryptionConfig
apiVersion: v1
resources:
- resources:
  - secrets
  providers:
  - aescbc:
      keys:
      - name: key1
        secret: \$ENCRYPTION_KEY
  - identity: {}
EOL
EOF

echo "[+] 写入 apiserver 配置"
cat > /etc/kubernetes/apiserver.conf <<EOF
KUBE_ADVERTIS_ADDRESS="--advertise-address=${MASTER_IP}"
KUBE_BIND_ADDRESS="--bind-address=${MASTER_IP}"
KUBE_ALLOW_PRIVILEGED="--allow-privileged=true"
KUBE_AUTHORIZATION_MODE="--authorization-mode=Node,RBAC"
KUBE_ENABLE_ADMISSION_PLUGINS="--enable-admission-plugins=NamespaceLifecycle,NodeRestriction,LimitRanger,ServiceAccount,DefaultStorageClass,ResourceQuota"
KUBE_SECURE_PORT="--secure-port=6443"
KUBE_ETCD_CAFILE="--etcd-cafile=${CERT_DIR}/ca.pem"
KUBE_ETCD_CERTFILE="--etcd-certfile=${CERT_DIR}/etcd.pem"
KUBE_ETCD_KEYFILE="--etcd-keyfile=${CERT_DIR}/etcd-key.pem"
KUBE_ETCD_SERVERS="--etcd-servers=https://${MASTER_IP}:2379"
KUBE_CLIENT_CA_FILE="--client-ca-file=${CERT_DIR}/ca.pem"
KUBE_KUBELET_CERT_AUTH="--kubelet-certificate-authority=${CERT_DIR}/ca.pem"
KUBE_KUBELET_CLIENT_CERT="--kubelet-client-certificate=${CERT_DIR}/apiserver.pem"
KUBE_KUBELET_CLIENT_KEY="--kubelet-client-key=${CERT_DIR}/apiserver-key.pem"
KUBE_PROXY_CLIENT_CERT_FILE="--proxy-client-cert-file=${CERT_DIR}/front-proxy-client.pem"
KUBE_PROXY_CLIENT_KEY_FILE="--proxy-client-key-file=${CERT_DIR}/front-proxy-client-key.pem"
KUBE_TLS_CERT_FILE="--tls-cert-file=${CERT_DIR}/apiserver.pem"
KUBE_TLS_PRIVATE_KEY_FILE="--tls-private-key-file=${CERT_DIR}/apiserver-key.pem"
KUBE_SERVICE_CLUSTER_IP_RANGE="--service-cluster-ip-range=10.32.0.0/16"
KUBE_SERVICE_ACCOUNT_ISSUER="--service-account-issuer=https://kubernetes.default.svc.cluster.local"
KUBE_SERVICE_ACCOUNT_KEY_FILE="--service-account-key-file=${CERT_DIR}/service-account.pem"
KUBE_SERVICE_ACCOUNT_SIGN_KEY_FILE="--service-account-signing-key-file=${CERT_DIR}/service-account-key.pem"
KUBE_SERVICE_NODE_PORT_RANGE="--service-node-port-range=30000-32767"
KUBE_ENABLE_AGG_ROUTE="--enable-aggregator-routing=true"
KUBE_REQUEST_HEADER_CA="--requestheader-client-ca-file=${CERT_DIR}/front-proxy-ca.pem"
KUBE_REQUEST_HEADER_ALLOWED_NAME="--requestheader-allowed-names=front-proxy-client"
KUBE_REQUEST_HEADER_EXTRA_HEADER_PREF="--requestheader-extra-headers-prefix=X-Remote-Extra-"
KUBE_REQUEST_HEADER_GROUP_HEADER="--requestheader-group-headers=X-Remote-Group"
KUBE_REQUEST_HEADER_USERNAME_HEADER="--requestheader-username-headers=X-Remote-User"
KUBE_ENCRYPTION_PROVIDER_CONF="--encryption-provider-config=${CERT_DIR}/encryption-config.yaml"
KUBE_API_ARGS="--request-timeout=120s"
EOF

echo "[+] 写入 systemd kube-apiserver.service"
cat > /usr/lib/systemd/system/kube-apiserver.service <<EOF
[Unit]
Description=Kubernetes API Server
After=network.target etcd.service

[Service]
EnvironmentFile=-/etc/kubernetes/apiserver.conf
ExecStart=/usr/bin/kube-apiserver \\
\$KUBE_ADVERTIS_ADDRESS \\
\$KUBE_BIND_ADDRESS \\
\$KUBE_ALLOW_PRIVILEGED \\
\$KUBE_AUTHORIZATION_MODE \\
\$KUBE_ENABLE_ADMISSION_PLUGINS \\
\$KUBE_SECURE_PORT \\
\$KUBE_ETCD_CAFILE \\
\$KUBE_ETCD_CERTFILE \\
\$KUBE_ETCD_KEYFILE \\
\$KUBE_ETCD_SERVERS \\
\$KUBE_CLIENT_CA_FILE \\
\$KUBE_KUBELET_CERT_AUTH \\
\$KUBE_KUBELET_CLIENT_CERT \\
\$KUBE_KUBELET_CLIENT_KEY \\
\$KUBE_PROXY_CLIENT_CERT_FILE \\
\$KUBE_PROXY_CLIENT_KEY_FILE \\
\$KUBE_TLS_CERT_FILE \\
\$KUBE_TLS_PRIVATE_KEY_FILE \\
\$KUBE_SERVICE_CLUSTER_IP_RANGE \\
\$KUBE_SERVICE_ACCOUNT_ISSUER \\
\$KUBE_SERVICE_ACCOUNT_KEY_FILE \\
\$KUBE_SERVICE_ACCOUNT_SIGN_KEY_FILE \\
\$KUBE_SERVICE_NODE_PORT_RANGE \\
\$KUBE_ENABLE_AGG_ROUTE \\
\$KUBE_REQUEST_HEADER_CA \\
\$KUBE_REQUEST_HEADER_ALLOWED_NAME \\
\$KUBE_REQUEST_HEADER_EXTRA_HEADER_PREF \\
\$KUBE_REQUEST_HEADER_GROUP_HEADER \\
\$KUBE_REQUEST_HEADER_USERNAME_HEADER \\
\$KUBE_ENCRYPTION_PROVIDER_CONF \\
\$KUBE_API_ARGS
Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# controller-manager 配置
echo "[+] 写入 kube-controller-manager 配置"
cat > /etc/kubernetes/controller-manager.conf <<EOF
KUBE_BIND_ADDRESS="--bind-address=127.0.0.1"
KUBE_ALLOCATE_CIDR="--allocate-node-cidrs=true"
KUBE_CLUSTER_CIDR="--cluster-cidr=10.244.0.0/16"
KUBE_CLUSTER_SIGNING_CERT_FILE="--cluster-signing-cert-file=${CERT_DIR}/ca.pem"
KUBE_CLUSTER_SIGNING_KEY_FILE="--cluster-signing-key-file=${CERT_DIR}/ca-key.pem"
KUBE_KUBECONFIG="--kubeconfig=${KUBECONFIG_DIR}/kube-controller-manager.kubeconfig"
KUBE_ROOT_CA_FILE="--root-ca-file=${CERT_DIR}/ca.pem"
KUBE_SERVICE_ACCOUNT_PRIVATE_KEY_FILE="--service-account-private-key-file=${CERT_DIR}/service-account-key.pem"
KUBE_SERVICE_CLUSTER_IP_RANGE="--service-cluster-ip-range=10.32.0.0/24"
KUBE_USE_SERVICE_ACCOUNT_CRED="--use-service-account-credentials=true"
KUBE_CONTROLLER="--controllers=*,bootstrapsigner,tokencleaner"
KUBE_SIGN_DURA="--cluster-signing-duration=876000h0m0s"
KUBE_REQUEST_CLIENT_CA="--requestheader-client-ca-file=${CERT_DIR}/front-proxy-ca.pem"
KUBE_LEADER_ELECT="--leader-elect=true"
KUBE_CONTROLLER_MANAGER_ARGS="--v=2"
EOF

cat > /usr/lib/systemd/system/kube-controller-manager.service <<EOF
[Unit]
Description=Kubernetes Controller Manager
[Service]
EnvironmentFile=-/etc/kubernetes/controller-manager.conf
ExecStart=/usr/bin/kube-controller-manager \\
\$KUBE_BIND_ADDRESS \\
\$KUBE_ALLOCATE_CIDR \\
\$KUBE_CLUSTER_CIDR \\
\$KUBE_CLUSTER_SIGNING_CERT_FILE \\
\$KUBE_CLUSTER_SIGNING_KEY_FILE \\
\$KUBE_KUBECONFIG \\
\$KUBE_ROOT_CA_FILE \\
\$KUBE_SERVICE_ACCOUNT_PRIVATE_KEY_FILE \\
\$KUBE_SERVICE_CLUSTER_IP_RANGE \\
\$KUBE_USE_SERVICE_ACCOUNT_CRED \\
\$KUBE_CONTROLLER \\
\$KUBE_SIGN_DURA \\
\$KUBE_REQUEST_CLIENT_CA \\
\$KUBE_LEADER_ELECT \\
\$KUBE_CONTROLLER_MANAGER_ARGS
Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# scheduler 配置
echo "[+] 写入 kube-scheduler 配置"
cat > /etc/kubernetes/scheduler.conf <<EOF
KUBE_CONFIG="--kubeconfig=${KUBECONFIG_DIR}/kube-scheduler.kubeconfig"
KUBE_AUTHENTICATION_KUBE_CONF="--authentication-kubeconfig=${KUBECONFIG_DIR}/kube-scheduler.kubeconfig"
KUBE_AUTHORIZATION_KUBE_CONF="--authorization-kubeconfig=${KUBECONFIG_DIR}/kube-scheduler.kubeconfig"
KUBE_BIND_ADDR="--bind-address=127.0.0.1"
KUBE_LEADER_ELECT="--leader-elect=true"
KUBE_SCHEDULER_ARGS="--v=2"
EOF

cat > /usr/lib/systemd/system/kube-scheduler.service <<EOF
[Unit]
Description=Kubernetes Scheduler Plugin
[Service]
EnvironmentFile=-/etc/kubernetes/scheduler.conf
ExecStart=/usr/bin/kube-scheduler \\
\$KUBE_CONFIG \\
\$KUBE_AUTHENTICATION_KUBE_CONF \\
\$KUBE_AUTHORIZATION_KUBE_CONF \\
\$KUBE_BIND_ADDR \\
\$KUBE_LEADER_ELECT \\
\$KUBE_SCHEDULER_ARGS
Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

echo "[+] 启动控制面组件"
mkdir -p /root/.kube
cp admin.kubeconfig /root/.kube/config

systemctl daemon-reload
systemctl enable kube-apiserver kube-controller-manager kube-scheduler
systemctl restart kube-apiserver kube-controller-manager kube-scheduler


# 等待组件完全启动
echo "[+] 等待 kube-apiserver 等组件启动中...（10 秒）"
sleep 10

export KUBECONFIG=/root/.kube/config

echo "[+] 配置 kube-apiserver RBAC 访问 kubelet"
cat > admin_cluster_role.yaml <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: system:kube-apiserver-to-kubelet
rules:
- apiGroups: [""]
  resources:
  - nodes/proxy
  - nodes/stats
  - nodes/log
  - nodes/spec
  - nodes/metrics
  verbs: ["*"]
EOF

cat > admin_cluster_rolebind.yaml <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: system:kube-apiserver
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: system:kube-apiserver-to-kubelet
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: User
  name: kube-apiserver
EOF

systemctl restart etcd
sleep 10
echo "等待etcd重启"

kubectl apply -f admin_cluster_role.yaml
kubectl apply -f admin_cluster_rolebind.yaml

echo "[√] 控制面部署完成"



# 获取 node 节点的用户名
read -p "请输入 node 节点用户名(例如root): " NODE_USER

# 创建目录和配置文件
echo "[+] 正在创建 kubeconfig 和相关配置文件..."

mkdir -p /etc/kubernetes/pki

# 开放端口
echo "[+] 开放 10250/tcp 端口..."
sudo firewall-cmd --zone=public --add-port=10250/tcp --permanent
sudo firewall-cmd --reload

sudo firewall-cmd --zone=public --add-port=443/tcp --permanent
sudo firewall-cmd --reload
sudo firewall-cmd --zone=public --add-port=4443/tcp --permanent
sudo firewall-cmd --reload

# 创建 kubeconfig 文件
echo "[+] 创建 k8snode1 的 kubeconfig 配置文件..."

kubectl config set-cluster eulixos-k8s \
  --certificate-authority=/etc/kubernetes/pki/ca.pem \
  --embed-certs=true \
  --server=https://$MASTER_IP:6443 \
  --kubeconfig=k8snode1.kubeconfig

kubectl config set-credentials system:node:k8snode1 \
  --client-certificate=/etc/kubernetes/pki/k8snode1.pem \
  --client-key=/etc/kubernetes/pki/k8snode1-key.pem \
  --embed-certs=true \
  --kubeconfig=k8snode1.kubeconfig

kubectl config set-context default \
  --cluster=eulixos-k8s \
  --user=system:node:k8snode1 \
  --kubeconfig=k8snode1.kubeconfig

kubectl config use-context default --kubeconfig=k8snode1.kubeconfig

# 创建 kube-proxy 配置文件
echo "[+] 创建 kube-proxy 的配置文件..."

kubectl config set-cluster eulixos-k8s \
  --certificate-authority=/etc/kubernetes/pki/ca.pem \
  --embed-certs=true \
  --server=https://$MASTER_IP:6443 \
  --kubeconfig=kube-proxy.kubeconfig

kubectl config set-credentials system:kube-proxy \
  --client-certificate=/etc/kubernetes/pki/kube-proxy.pem \
  --client-key=/etc/kubernetes/pki/kube-proxy-key.pem \
  --embed-certs=true \
  --kubeconfig=kube-proxy.kubeconfig

kubectl config set-context default \
  --cluster=eulixos-k8s \
  --user=system:kube-proxy \
  --kubeconfig=kube-proxy.kubeconfig

kubectl config use-context default --kubeconfig=kube-proxy.kubeconfig

# 传输配置文件到 node 节点
echo "[+] 正在传输配置文件到 node 节点 $NODE_IP..."

scp -r /etc/kubernetes/pki ${NODE_USER}@${NODE_IP}:/etc/kubernetes/
scp -r /root/k8snode1.kubeconfig ${NODE_USER}@${NODE_IP}:/etc/kubernetes/pki/
scp -r /root/kube-proxy.kubeconfig ${NODE_USER}@${NODE_IP}:/etc/kubernetes/pki/

echo "[+] 配置文件已传输到 node 节点 $NODE_IP。"

echo "复制etcd证书到node节点"
echo "请输入 node 节点的 IP 地址："
read NODE_IP

# 远程 node 用户
NODE_USER=root

# 本地证书路径
CERT_DIR=/root

# 远程路径
REMOTE_ETCD_DIR=/etc/etcd
REMOTE_DATA_DIR=/var/lib/etcd

echo "[+] 正在创建 node 节点目录..."
ssh ${NODE_USER}@${NODE_IP} "mkdir -p ${REMOTE_ETCD_DIR} ${REMOTE_DATA_DIR}"

echo "[+] 正在复制 etcd 证书到 node 节点..."
scp ${CERT_DIR}/ca.pem ${NODE_USER}@${NODE_IP}:${REMOTE_ETCD_DIR}/
scp ${CERT_DIR}/etcd.pem ${NODE_USER}@${NODE_IP}:${REMOTE_ETCD_DIR}/
scp ${CERT_DIR}/etcd-key.pem ${NODE_USER}@${NODE_IP}:${REMOTE_ETCD_DIR}/

echo "[√] etcd 证书成功复制并配置到 node 节点 ${NODE_IP}"
