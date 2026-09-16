#!/bin/bash

set -euo pipefail
trap 'echo "❌ 脚本失败，出错行：$LINENO，退出码：$?"' ERR

#配置k8s其他的组件，在master节点上
echo "[INFO] 开始配置防火墙..."
sudo firewall-cmd --zone=public --add-port=8472/udp --permanent
sudo firewall-cmd --reload
echo "[INFO] 防火墙配置完成。"

echo "[INFO] 开始部署 Flannel 网络插件..."
kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml
echo "[INFO] Flannel 部署完成。"

echo "[SUCCESS] 网络插件安装脚本执行完毕。"

echo "[INFO] 下载 metrics-server 组件 YAML..."
wget -q https://raw.githubusercontent.com/2374509838/K8S-jiaoben/master/components.yaml

echo "[INFO] 应用 YAML..."
kubectl apply -f components.yaml


echo "[INFO] 创建 RBAC 配置..."
cat > extension-apiserver-rbac.yaml <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: extension-apiserver-authentication-reader
rules:
- apiGroups: [""]
  resources: ["configmaps"]
  resourceNames: ["extension-apiserver-authentication"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: metrics-server-auth-reader-binding
subjects:
- kind: ServiceAccount
  name: metrics-server
  namespace: kube-system
roleRef:
  kind: ClusterRole
  name: extension-apiserver-authentication-reader
  apiGroup: rbac.authorization.k8s.io
EOF

kubectl apply -f extension-apiserver-rbac.yaml

echo "[SUCCESS] metrics-server 安装与配置完成。"


echo "正在生成 CoreDNS 配置文件..."

cat > coredns.yaml << 'EOF'
# This is a file generated from the base underscore template file:
# coredns.yaml.base
apiVersion: v1
kind: ServiceAccount
metadata:
  name: coredns
  namespace: kube-system
  labels:
    kubernetes.io/cluster-service: "true"
    addonmanager.kubernetes.io/mode: Reconcile
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  labels:
    kubernetes.io/bootstrapping: rbac-defaults
    addonmanager.kubernetes.io/mode: Reconcile
  name: system:coredns
rules:
- apiGroups: [""]
  resources: ["endpoints", "services", "pods", "namespaces"]
  verbs: ["list", "watch"]
- apiGroups: ["discovery.k8s.io"]
  resources: ["endpointslices"]
  verbs: ["list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  annotations:
    rbac.authorization.kubernetes.io/autoupdate: "true"
  labels:
    kubernetes.io/bootstrapping: rbac-defaults
    addonmanager.kubernetes.io/mode: EnsureExists
  name: system:coredns
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: system:coredns
subjects:
- kind: ServiceAccount
  name: coredns
  namespace: kube-system
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns
  namespace: kube-system
  labels:
    addonmanager.kubernetes.io/mode: EnsureExists
data:
  Corefile: |
    .:53 {
        errors
        health {
            lameduck 5s
        }
        ready
        kubernetes cluster.local in-addr.arpa ip6.arpa {
            pods insecure
            fallthrough in-addr.arpa ip6.arpa
            ttl 30
        }
        prometheus :9153
        forward . /etc/resolv.conf {
            max_concurrent 1000
        }
        cache 30
        loop
        reload
        loadbalance
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: coredns
  namespace: kube-system
  labels:
    k8s-app: kube-dns
    kubernetes.io/cluster-service: "true"
    addonmanager.kubernetes.io/mode: Reconcile
    kubernetes.io/name: "CoreDNS"
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
  selector:
    matchLabels:
      k8s-app: kube-dns
  template:
    metadata:
      labels:
        k8s-app: kube-dns
    spec:
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      priorityClassName: system-cluster-critical
      serviceAccountName: coredns
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            podAffinityTerm:
              labelSelector:
                matchExpressions:
                - key: k8s-app
                  operator: In
                  values: ["kube-dns"]
              topologyKey: kubernetes.io/hostname
      tolerations:
      - key: "CriticalAddonsOnly"
        operator: "Exists"
      nodeSelector:
        kubernetes.io/os: linux
      containers:
      - name: coredns
        image: coredns/coredns:riscv64-1.12.0
        imagePullPolicy: IfNotPresent
        resources:
          limits:
            memory: 170Mi
          requests:
            cpu: 100m
            memory: 70Mi
        args: ["-conf", "/etc/coredns/Corefile"]
        volumeMounts:
        - name: config-volume
          mountPath: /etc/coredns
          readOnly: true
        ports:
        - containerPort: 53
          name: dns
          protocol: UDP
        - containerPort: 53
          name: dns-tcp
          protocol: TCP
        - containerPort: 9153
          name: metrics
          protocol: TCP
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
            scheme: HTTP
          initialDelaySeconds: 60
          timeoutSeconds: 5
          successThreshold: 1
          failureThreshold: 5
        readinessProbe:
          httpGet:
            path: /ready
            port: 8181
            scheme: HTTP
          initialDelaySeconds: 60
          timeoutSeconds: 5
          successThreshold: 1
          failureThreshold: 5
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            add: ["NET_BIND_SERVICE"]
            drop: ["ALL"]
          readOnlyRootFilesystem: true
      dnsPolicy: Default
      volumes:
      - name: config-volume
        configMap:
          name: coredns
          items:
          - key: Corefile
            path: Corefile
---
apiVersion: v1
kind: Service
metadata:
  name: kube-dns
  namespace: kube-system
  annotations:
    prometheus.io/port: "9153"
    prometheus.io/scrape: "true"
  labels:
    k8s-app: kube-dns
    kubernetes.io/cluster-service: "true"
    addonmanager.kubernetes.io/mode: Reconcile
    kubernetes.io/name: "CoreDNS"
spec:
  selector:
    k8s-app: kube-dns
  ports:
  - name: dns
    port: 53
    protocol: UDP
  - name: dns-tcp
    port: 53
    protocol: TCP
  - name: metrics
    port: 9153
    protocol: TCP
EOF

echo "✅ CoreDNS 配置文件 coredns.yaml 已生成。"

echo "������ 下载原始 MPI Operator 配置文件..."
wget -O mpi-operator.yaml https://raw.githubusercontent.com/kubeflow/mpi-operator/v0.6.0/deploy/v2beta1/mpi-operator.yaml

echo "������ 修改镜像为 cyber1010/mpi-operator-riscv64:latest..."
sed -i 's#mpioperator/mpi-operator:.*#cyber1010/mpi-operator-riscv64:latest#g' mpi-operator.yaml

echo "������ 部署 MPI Operator 到 Kubernetes..."
kubectl create -f mpi-operator.yaml

echo "✅ MPI Operator 部署完成。"
