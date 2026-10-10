# openEuler / RISC-V / cgroup v1：全部组件安装与使用手册

## 0. 本手册怎样执行

适用对象：已有 K8S-construct-master 构建脚本、RISC-V Linux 服务器、openEuler 22 系列、cgroup v1。本手册为平台附加组件的当前入口；迁移的更细步骤保留在 migration/README.md。

每个 bash 代码块是一条可单独执行的命令。包含 heredoc（例如 cat ... <<'EOF'）的整个代码块是一条命令，一次复制整个块，直到最后一行 EOF。示例中的 IP、节点名、镜像仓库和设备名必须先替换。

下文假设源码目录是 /opt/rv-platform-src，里面直接有 k8s_master.sh 和 platform。上传 Windows 源码时，应上传内层 K8S-construct-master 目录内容。Linux 上统一使用 LF 换行。

### 0.1 机器分工

| 角色 | 示例名称/IP | 运行内容 |
|---|---|---|
| 控制机 | control / 192.168.50.70 | K8s 控制面、平台控制器、迁移控制器、kubectl/Helm |
| 执行节点 1 | worker1 / 192.168.50.71 | kubelet/containerd/runc、节点 exporter、迁移代理、CNI、可选设备插件 |
| 执行节点 2 | worker2 / 192.168.50.72 | 同上 |
| 更多执行节点 | worker3 ... | 按同样步骤加入，控制器节点表不限制为两台 |

你当前两台机器可以先装控制机 + worker1。跨节点迁移和真正跨执行节点的通信测试，需要至少两个执行节点；保持控制机只做控制面时，需要再加入 worker2。一个 Worker 上运行多个 rank 只能验证节点内通信。MPI_WORKERS=1、MPI_SLOTS=2 可以在现有单 Worker 上做功能自检，不能将其结果写为跨节点性能。

### 0.2 交付范围和验证状态

如果服务器已安装旧直通版本，先执行第 6.5 节，保持旧控制器运行直至旧任务全部回收，再执行第 4.2/6.2 节更新控制器。安装器发现尚有旧 DirectWorkload 时会拒绝覆盖，避免旧 finalizer 无人处理。

所有列出的组件均有源码和安装/测试入口，见 comparison.md。它们共同构成首版实现，真实平台仍需联调。

迁移按已确认的需求采用保存进程内存的停顿式方案，允许业务暂停；预拷贝和零停顿不列为必做项，不再继续优化停顿时间。测试仍需核对跨节点恢复、内存状态延续和失败处理，并记录实际暂停时间。当前不支持任意卷/已有 TCP/加速卡状态。设备插件需要现成可用的内核驱动。Istio 需要真正可运行的匹配版 RISC-V Envoy；上游构建可能需要额外移植补丁。普通 Linux/K8s 不能仅凭本项目代码保证硬实时。不要把这些条件理解为已经验收通过。

## 1. 每台机器先记录系统

以下每条分别执行：

~~~bash
uname -m
~~~

应为 riscv64。

~~~bash
cat /etc/os-release
~~~

~~~bash
uname -r
~~~

~~~bash
findmnt -t cgroup,cgroup2,tmpfs
~~~

cgroup v1 应能看到 cpu/memory/cpuset 等单独 cgroup 挂载；根目录常为 tmpfs。

~~~bash
date -Is
~~~

所有节点保持时钟同步，Prometheus 时间戳去重和新鲜度检查依赖正常时钟。

~~~bash
df -h /var/lib/containerd /var/lib
~~~

检查点可能接近任务内存加可写层大小，源、目标节点都需有足够磁盘空间。

### 1.1 软件依赖

控制机：

~~~bash
dnf install -y python3 openssh-clients git curl tar gzip make gcc openssl skopeo
~~~

执行节点：

~~~bash
dnf install -y python3 openssh-clients openssh-server iproute runc criu gcc make openmpi openmpi-devel
~~~

软件源中包不存在时，先配置与你的 openEuler/RISC-V 版本匹配的软件源，再继续。不要下载 amd64 RPM 代替。特别是 CRIU、Open MPI、Node.js、Yarn/Bazel 的 RISC-V 供应情况取决于当前仓库。

~~~bash
dnf list --showduplicates kubernetes-master kubernetes-node containerd
~~~

记录选定版本。原构建脚本仍由 dnf 安装 K8s，首次部署应在软件源/版本锁定后执行，不能假设不同时间安装得到同一版本。

### 1.2 Go：直接使用服务器现有的 1.21

Python 控制器无需 Go。迁移工具和设备插件已适配 Go 1.21，go.mod/go.sum 固定兼容依赖；不用安装 1.27，也不用替换现有 /usr/bin/go 或 PATH。

~~~bash
go version
~~~

应显示 go1.21.x。为了避免 Go 工具链自动升级，当前终端设置：

~~~bash
export GOTOOLCHAIN=local
~~~

~~~bash
export GOWORK=off
~~~

~~~bash
go env GOVERSION GOTOOLCHAIN
~~~

输出应是现有版本和 local。自有 Go 组件构建脚本也会自动设置这两个环境变量，并检查最低版本为 1.21，不依赖你手工 export 才能阻止升级。

不要执行 go get -u 或自行换成最新依赖，否则可能重新引入要求更高 Go 的模块。源码已包含锁定文件，按本手册构建即可；第一次编译仍需下载这些依赖。默认代理不可达时可在当前终端选择可访问的代理，校验保持启用：

~~~bash
export GOPROXY='https://proxy.golang.org|https://goproxy.cn|direct'
~~~

本地已用官方 Go 1.21.0 实际测试和交叉编译，覆盖 Go 1.21 系列的语言版本要求；目标 Linux/RISC-V 的运行验证仍按第 11 节执行。

迁移的 containerd daemon 与本项目 Go 工具是两个不同程序：原生恢复仍要求 containerd 2.1。完整包附带 Linux/riscv64 containerd 2.1.5、containerd-shim-runc-v2、ctr 和校验/源码信息，服务器可直接使用这些程序，不需要以 Go 1.21 编译 containerd 上游源码，也无需升级服务器 Go。是否需要切换现有运行时按迁移手册第 6 节判断。

Helm/Prometheus/Grafana/Istio 属于第三方项目，所选版本的编译器要求由其源码决定。第 8 节说明现有 Go 1.21 不兼容某上游版本时如何使用匹配的 RISC-V 产物；本项目 Go 1.21 兼容不表示所有第三方最新版也能用 1.21 构建。

## 2. 基础集群

### 2.1 已有集群

不要重新执行初始化脚本。先在控制机检查：

~~~bash
kubectl get nodes -o wide
~~~

~~~bash
kubectl version -o json
~~~

~~~bash
kubectl get pods -A -o wide
~~~

~~~bash
kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes
~~~

所有计划使用的 Worker 先达到 Ready，Flannel/CoreDNS/metrics-server 工作正常。HPA 测试使用 autoscaling/v2，需要服务端支持该 API（1.23+）；大纲中的 1.22 集群要升级或另行适配 v2beta2。Istio 所选版本还必须符合其官方 K8s 兼容表。

### 2.2 新建集群

控制机进入源码目录：

~~~bash
cd /opt/rv-platform-src
~~~

~~~bash
bash k8s_master.sh
~~~

按脚本提示填写控制 IP/名称和目标节点信息。脚本会安装控制组件并签发节点凭据。节点身份必须与后面执行节点输入相同。控制面 CA 私钥留在控制机。

执行节点进入同一源码目录：

~~~bash
cd /opt/rv-platform-src
~~~

~~~bash
bash k8s_node.sh
~~~

按提示输入该节点真实 IP/名称。该脚本会配置 containerd、kubelet、kube-proxy，不在节点另建 etcd。

控制机：

~~~bash
bash k8s_master_other_components.sh
~~~

该原脚本包含网络、DNS、metrics-server 和 MPI Operator。里面部分上游 URL/镜像仍是浮动引用：正式交付环境应保存下载文件、记录 digest/版本并使用审核后的版本。不要把安装脚本输出“完成”作为组件健康证明。

### 2.3 cgroup v1 配置

执行节点：

~~~bash
kubelet --version
~~~

~~~bash
cat /etc/kubernetes/pki/kubelet_config.yaml
~~~

应包含 cgroupDriver: systemd。containerd 的 runc runtime 同样使用 SystemdCgroup=true。

对于 Kubernetes 1.35+ 且仍支持 cgroup v1 的版本，kubelet 配置需要 failCgroupV1: false。当前 k8s_node.sh 会按版本写入；已有节点需按其真实版本手工配置后重启 kubelet。不要向不认识该字段的旧版本盲目添加。依据：[Kubernetes 官方说明](https://kubernetes.io/blog/2026/10/06/kubernetes-cgroups-v2-shift/)。

~~~bash
systemctl restart kubelet
~~~

~~~bash
journalctl -u kubelet -n 100 --no-pager
~~~

### 2.4 新增任意 Worker

先确保新节点的源码、依赖、IP/主机名准备好。控制机：

~~~bash
bash platform/install.sh node-credentials worker2 192.168.50.72 https://192.168.50.70:6443 root
~~~

然后在 worker2 执行 k8s_node.sh。新增 worker3/worker4 按同样步骤签发各自身份。不要复制控制面整个 /etc/kubernetes/pki，不要共用另一个 Worker 的证书。

控制机：

~~~bash
kubectl get nodes -o wide
~~~

## 3. 准备节点管理通道与配置

平台节点检查和迁移使用控制机到节点的 SSH。所有私钥只保存在控制机。

控制机：

~~~bash
ssh-keygen -t ed25519 -f /root/.ssh/rv-platform
~~~

~~~bash
ssh-copy-id -i /root/.ssh/rv-platform.pub root@192.168.50.71
~~~

~~~bash
ssh-copy-id -i /root/.ssh/rv-platform.pub root@192.168.50.72
~~~

首次连接时核对服务器指纹并写入 known_hosts：

~~~bash
ssh -i /root/.ssh/rv-platform root@192.168.50.71 hostname
~~~

~~~bash
ssh -i /root/.ssh/rv-platform root@192.168.50.72 hostname
~~~

~~~bash
install -d -m 0700 /etc/rv-platform
~~~

~~~bash
cp platform/control/config.example.json /etc/rv-platform/platform.json
~~~

~~~bash
vi /etc/rv-platform/platform.json
~~~

配置字段：

| 字段 | 含义 |
|---|---|
| kubeconfig | 固定 /etc/rv-platform/platform.kubeconfig，由安装器签发 |
| prometheusURL | 控制机能访问的 Prometheus URL；使用第 8 节查询到的 Service IP |
| modules | resource、network-host、scenario，可以独立开启 |
| pollSeconds | 控制循环周期，默认 10 秒 |
| nodes | 以 K8s Node 名为键，值为 SSH host/user/identityFile/port |
| lockFile | 可选，本机单进程锁路径；不要启动另一台控制机上的重复写控制器 |

下面是一条完整写文件命令。请替换 IP 和节点名后再执行：

~~~bash
cat > /etc/rv-platform/platform.json <<'EOF'
{
  "kubeconfig": "/etc/rv-platform/platform.kubeconfig",
  "prometheusURL": "http://127.0.0.1:9090",
  "pollSeconds": 10,
  "modules": ["resource", "network-host", "scenario"],
  "nodes": {
    "worker1": {"host": "192.168.50.71", "user": "root", "identityFile": "/root/.ssh/rv-platform"},
    "worker2": {"host": "192.168.50.72", "user": "root", "identityFile": "/root/.ssh/rv-platform"}
  }
}
EOF
~~~

127.0.0.1:9090 是待替换初值；只有你在控制机运行了相应转发/服务时才有效。

## 4. 安装节点感知与资源控制

### 4.1 每个 Worker

~~~bash
cd /opt/rv-platform-src
~~~

~~~bash
bash platform/install.sh node-agent
~~~

~~~bash
/usr/local/libexec/rv-node describe
~~~

~~~bash
systemctl status rv-node-exporter --no-pager
~~~

~~~bash
curl -fsS http://127.0.0.1:9108/metrics
~~~

网络访问：控制机 SSH TCP22；控制面 API TCP6443；kubelet TCP10250；Flannel UDP8472；节点指标 TCP9108。hostNetwork 业务端口仅对可信通信节点开放。9108 应只对监控/控制机开放，不面向公网。

### 4.2 控制机

~~~bash
bash platform/install.sh resource /etc/rv-platform/platform.json
~~~

resource/network-host/scenario 这三个入口安装同一个可选择模块的控制服务，模块列表以配置文件为准；不需要启动三份相互竞争的服务。可分别安装节点/迁移/生态部分。

~~~bash
systemctl status rv-platform-controller --no-pager
~~~

~~~bash
kubectl get crd
~~~

~~~bash
journalctl -u rv-platform-controller -n 100 --no-pager
~~~

首次 Prometheus 尚未启动时，场景对象会提示查询失败；资源和网络模块仍独立处理其对象。

### 4.3 配置节点池

~~~bash
cat > /etc/rv-platform/worker1-profile.yaml <<'EOF'
apiVersion: platform.riscv.io/v1alpha1
kind: NodeProfile
metadata:
  name: worker1-profile
  namespace: default
spec:
  node: worker1
  labels:
    platform.riscv.io/pool: general
  taints: []
  schedulable: true
EOF
~~~

~~~bash
kubectl apply -f /etc/rv-platform/worker1-profile.yaml
~~~

~~~bash
kubectl get nodeprofile worker1-profile -o yaml
~~~

worker2 创建名称不同、spec.node=worker2 的对象，设置相同池标签。一个节点只允许一个 NodeProfile 管理。只管理 platform.riscv.io/ 前缀的 labels/taints，其他组件的字段保留。schedulable=false 表示不再接收普通新任务，不会主动删除已有任务。删除配置对象不会自动反向修改节点，先通过编辑配置恢复所需状态。

### 4.4 分配、扩缩、调整资源

先将 platform/resource/examples.yaml 的 image 替换为你自己可运行的 RISC-V 镜像。创建后：

~~~bash
kubectl apply -f platform/resource/examples.yaml
~~~

~~~bash
kubectl get resourceclaims.platform.riscv.io -o wide
~~~

~~~bash
kubectl get deployment compute-pool -o yaml
~~~

~~~bash
kubectl get pods -o wide
~~~

扩至 4 副本：

~~~bash
kubectl patch resourceclaim.platform.riscv.io compute-pool --type=merge -p '{"spec":{"replicas":4}}'
~~~

修改 CPU 限额：

~~~bash
kubectl patch resourceclaim.platform.riscv.io compute-pool --type=merge -p '{"spec":{"limits":{"cpu":"1","memory":"128Mi"}}}'
~~~

这些操作通过 Deployment/调度器和 kubelet 生效，资源调整使用滚动更新。不是不重启进程的原地 CPU/内存伸缩。修改 nodeSelector 可以滚动转移无状态副本；需要保留进程状态时使用 NodeMigration，而当前 NodeMigration 不接受 Deployment 管理的 Pod。

释放：

~~~bash
kubectl delete resourceclaim.platform.riscv.io compute-pool
~~~

其 Deployment 通过 ownerReference 自动回收。启用外部 HPA/ResourcePolicy/ScenarioPolicy 控制时，ResourceClaim.spec.externalScaling=true，防止资源控制器把副本数写回固定值。Deployment 的 allow-scaling 注解需按第 9 节设置。

## 5. 可选的真实设备接入

普通 CPU/内存不需要设备插件。需要加速器/其他 /dev 设备时，先确认厂商驱动在 RISC-V Linux 正常工作。该组件不替代驱动。

执行节点：

~~~bash
ls -l /dev
~~~

~~~bash
cp platform/device-plugin/config.example.json /etc/rv-platform/devices.json
~~~

~~~bash
vi /etc/rv-platform/devices.json
~~~

用真实设备替换 hostPath/containerPath，resourceName 为例如 hardware.riscv.io/accelerator；每个设备一个唯一 id。permissions 为需要的 r/w/m 权限。numa 可省略。

~~~bash
bash platform/install.sh device-node /etc/rv-platform/devices.json
~~~

~~~bash
systemctl status rv-device-plugin --no-pager
~~~

控制机：

~~~bash
kubectl get node worker1 -o json
~~~

status.allocatable 应出现 hardware.riscv.io/accelerator。节点路径不是字符/块设备时会标记 Unhealthy。设备独占数量由 kubelet 记账。多个不同类型可通过不同服务/config 启动独立实例；默认安装器只创建一个资源类型的服务。

在 ResourceClaim 的 requests 和 limits 同时加入：

~~~yaml
hardware.riscv.io/accelerator: "1"
~~~

扩展资源 request/limit 必须相等且是整数。应用仍要自己调用该设备的驱动接口。不会将远端设备透明映射到本机，也不会替设备迁移内部状态。

## 6. 最简软件网络：hostNetwork + TCP

### 6.1 方案与前提

通信容器直接使用执行节点现有网络，以“节点 IP + TCP 端口”访问。不需要专用网卡、SR-IOV、RDMA、Soft-RoCE、Multus 或额外 CNI。普通业务 Pod 继续使用原 Flannel，网络对照自动使用这条路径作为基线。

这是绕过 Pod overlay 的软件方案，没有把物理网卡/VF 分配给容器，不能描述为已实现物理网卡直通。吞吐仍受已有链路、CPU 和 TCP 栈限制，是否提升由第 11.4 节实测决定。

控制机确认真实节点名及 Internal-IP：

~~~bash
kubectl get nodes -o wide
~~~

节点名以此输出为准，终端提示符可能不同。你已有 192.168.102.125/24 和 10.27.180.154/26；选择所有参与节点都能互通的一张现有网络。示例网段 192.168.102.0/24 需核对其他节点后再使用。不需要启用当前 NO-CARRIER 的 enP1p131s0f1。

在参与 Worker 检查：

~~~bash
ip -br address
~~~

~~~bash
ip route show
~~~

在另一参与节点检查目标 IP，替换实际地址：

~~~bash
ping -c 3 192.168.102.125
~~~

ICMP 被过滤时以真实 TCP 连通为准。业务 TCP 端口如 25201 仅对可信通信节点放行。MPI 使用 25222 及运行时动态分配的控制/数据 TCP 端口，需要允许参与节点之间的相应连接；只放行 SSH 端口不足以运行 MPI。按来源节点配置防火墙，不修改已有管理服务。

### 6.2 控制机安装

完成第 3 节配置，modules 使用 resource、network-host、scenario。三个模块由同一控制服务运行，下面命令安装/更新该服务：

~~~bash
bash platform/install.sh network-host /etc/rv-platform/platform.json
~~~

~~~bash
systemctl status rv-platform-controller --no-pager
~~~

~~~bash
kubectl get crd hostnetworkworkloads.platform.riscv.io
~~~

网络模块只访问 K8s API，无需 Worker SSH 凭据。SSH 表仍供节点管理和迁移使用。

### 6.3 镜像和任务

先按第 7.2 节构建并推送新的 RISC-V 工具镜像 rv-communication:0.2.0，所有节点使用同一镜像。修改示例：

~~~bash
vi platform/network-host/example.yaml
~~~

REPLACE_WORKER1 换成真实 K8s Node 名；REPLACE_WITH_RISCV64_TOOLS_IMAGE 换成镜像地址。默认服务运行 iperf3 -s -p 25201；修改监听端口时同步修改 ports 中的 containerPort。

| 字段 | 含义 |
|---|---|
| node | 通过调度亲和性指定节点，不绕过 scheduler |
| image | 可在目标 RISC-V 节点运行的镜像 |
| command / args / env | 容器启动内容 |
| ports | 列出实际监听的全部 TCP 端口，1024～65535 |
| requests / limits | CPU/内存调度记账和 cgroup 限额 |
| tolerations / imagePullSecrets | 可选污点容忍和仓库凭据 |

只发起连接的客户端可使用 ports: []。同节点任务必须选择不同监听端口。接口拒绝旧 device/mac/address/gateway，不提供 UDP 或静态 IP 分配。

~~~bash
kubectl apply -f platform/network-host/example.yaml
~~~

示例创建 rv-network namespace，将这个 namespace 的 Pod Security 设置为 privileged 级别，允许 hostNetwork 准入；不会自动赋予容器 privileged:true。只在专用 namespace 放置可信任务，其他准入策略仍需管理员允许 hostNetwork。

~~~bash
kubectl get hostnetworkworkloads.platform.riscv.io -n rv-network
~~~

~~~bash
kubectl get hostnetworkworkload tcp-server -n rv-network -o yaml
~~~

~~~bash
kubectl get pods -n rv-network -o wide
~~~

状态包含 pod、node、nodeIP、ports、phase、ready、mode。等待控制周期（默认 10 秒）和镜像下载。Running/ready=true 表示容器运行；任意自定义服务还需检查实际监听。网络自动测试有额外 TCP 连通探测。

另一台可访问目标节点的 Linux 安装客户端：

~~~bash
dnf install -y iperf3
~~~

把地址换成目标 Worker IP：

~~~bash
iperf3 -c 192.168.102.125 -p 25201 -t 30 -P 4
~~~

这是手工连通自检，正式比较按第 11.4 节运行。

### 6.4 端口、日志、回收

控制器跨 namespace 检查本模块节点/端口冲突，并生成 hostPort 供 scheduler 记账。主机进程不在 K8s 端口账本中，选择端口前在目标 Worker 检查：

~~~bash
ss -ltnp
~~~

主机进程占用端口会导致应用绑定失败；改用空闲端口后删除重建任务，不终止服务器已有 SSH 服务。所有监听端口应如实声明，漏报或绕过接口的服务无法完整预留。

查询 Pod 名后替换 POD_NAME：

~~~bash
kubectl logs -n rv-network POD_NAME
~~~

~~~bash
kubectl describe pod -n rv-network POD_NAME
~~~

正常回收：

~~~bash
kubectl delete hostnetworkworkload tcp-server -n rv-network --wait=true --timeout=120s
~~~

先正常删除 Pod，确认对象消失且节点 Ready 后释放 finalizer。节点失联时保留预留，恢复后继续，不强删 Pod/finalizer。已分配 spec 不允许原地修改，修改节点、镜像、端口时先删除再创建。

hostNetwork 共享主机网络命名空间，CPU/内存 cgroup 隔离仍保留。网络隔离较弱，不能默认原 Pod NetworkPolicy 对它同样生效。该方案不修改宿主地址、默认路由或 CNI，无需清理网卡地址。

### 6.5 从旧版本升级

此次已删除旧直通源码/脚本。服务器未安装旧版时直接安装。已经安装时，在覆盖旧控制器前检查：

~~~bash
kubectl get crd directworkloads.platform.riscv.io
~~~

CRD 存在时再检查：

~~~bash
kubectl get directworkloads.platform.riscv.io -A
~~~

逐个填入实际名字，由仍运行的旧控制器正常释放任务和 finalizer：

~~~bash
kubectl delete directworkloads.platform.riscv.io OLD_NAME -n OLD_NAMESPACE --wait=true --timeout=120s
~~~

确认列表为空后再安装新控制器；新控制器不处理旧 DirectWorkload。

节点若曾安装 Multus，确认其他依赖任务已经迁走、网口已归还后，依据节点上的原 CNI 备份恢复默认入口。其他业务仍依赖 Multus 时可以保留，hostNetwork 不要求卸载。不要清空 /etc/cni/net.d 或覆盖仍使用的配置。本次没有连接远端服务器，源码替换不表示远端已经卸载。

旧任务清零、关联 NAD 回收后可删除本项目旧 CRD/专用凭据。仅在上述前提核实后执行，对象不存在则跳过：

~~~bash
kubectl delete crd directworkloads.platform.riscv.io
~~~

~~~bash
kubectl delete clusterrolebinding rv-multus
~~~

~~~bash
kubectl delete clusterrole rv-multus
~~~

~~~bash
kubectl delete serviceaccount rv-multus -n rv-platform
~~~

~~~bash
kubectl delete secret rv-multus-token -n rv-platform
~~~

不要删除集群共享的 network-attachment-definitions CRD。

## 7. MPI 分层通信与计算通信融合

### 7.1 本机编译

在 RISC-V Linux（控制机或 Worker 均可）：

~~~bash
bash platform/install.sh collective
~~~

fusion 入口构建同一个库和测试程序，算法源码同时包含在里面，无需重复编译：

~~~bash
ls -l platform/communication/bin/rv-bench
~~~

本机自检（仅验证当前节点）：

~~~bash
mpirun --allow-run-as-root -np 2 platform/communication/bin/rv-bench bcast hierarchical 1024 7 256 32 1
~~~

~~~bash
mpirun --allow-run-as-root -np 2 platform/communication/bin/rv-bench reduce hierarchical 1024 7 256 32 1
~~~

~~~bash
mpirun --allow-run-as-root -np 2 platform/communication/bin/rv-bench allgather hierarchical 1024 7 256 32 0
~~~

~~~bash
mpirun --allow-run-as-root -np 2 platform/communication/bin/rv-bench fusion overlap 65536 7 4096 32 0
~~~

每条末尾输出 JSON：correct、relative_error、mantissa_bits、mean_seconds、nodes 等。long double 的精度取决于 ABI，本程序记录实际位数；不能把所有平台的 long double 自动写成 FP128。不同进程精度不一致会拒绝，避免静默降精度。

### 7.2 构建 RISC-V MPI 镜像

选择能在本机运行且具备匹配 dnf 软件源的 RISC-V openEuler 基础镜像。以下替换后执行：

~~~bash
docker build --build-arg BASE_IMAGE=YOUR_RISCV64_OPENEULER_IMAGE -t YOUR_REGISTRY/rv-communication:0.2.0 platform/communication
~~~

~~~bash
docker push YOUR_REGISTRY/rv-communication:0.2.0
~~~

可用兼容 build/push 的 Podman/nerdctl 替代 docker。先在 RISC-V 执行镜像的 python3/mpirun/rv-bench/sshd，确认工具完整。多架构集群必须为每种架构分别构建；不能把 RISC-V ELF 放进 amd64 镜像运行。

### 7.3 MPI Operator

原基础脚本已有 v0.6.0 的部署入口。检查：

~~~bash
kubectl get crd mpijobs.kubeflow.org
~~~

~~~bash
kubectl get pods -A -o wide
~~~

Operator 本身也必须是可运行的 RISC-V 镜像。任务采用 v2beta1 MPIJob；MPI Operator 负责 SSH 密钥/hostfile 与 Worker/Launcher 生命周期。

### 7.4 任务生成

新镜像包含 TCP 启动器及 Worker 身份采集，需重新构建 0.2.0，不能复用旧 tag 缓存。核对所有参与节点共同可达的物理 IPv4 网段。示例 192.168.102.0/24 需换成实际共同网段。

创建可信通信任务 namespace：

~~~bash
kubectl create namespace rv-mpi
~~~

~~~bash
kubectl label namespace rv-mpi pod-security.kubernetes.io/enforce=privileged --overwrite
~~~

~~~bash
python3 platform/communication/job.py --image YOUR_REGISTRY/rv-communication:0.2.0 --operation bcast --mode hierarchical --workers 2 --slots 2 --network host --tcp-network 192.168.102.0/24 --ssh-port 25222 --spread --namespace rv-mpi --name rv-bcast > /etc/rv-platform/rv-bcast.json
~~~

~~~bash
kubectl apply -f /etc/rv-platform/rv-bcast.json
~~~

~~~bash
kubectl get mpijob rv-bcast -n rv-mpi -o yaml
~~~

~~~bash
kubectl get pods -n rv-mpi -o wide
~~~

Worker 容器内 sshd 使用 25222，不影响服务器 SSH:22。Worker 声明 hostPort、强制每节点一个 Worker；多个同端口 MPIJob 应串行运行或改用不同 --ssh-port。Launcher 等 Worker 就绪后启动，--slots=2 表示每 Worker 两个 rank，Launcher 由 K8s 调度。

launch.py 选择 Open MPI ob1 + TCP，保留可用的节点内 sm/vader 共享内存。TCP 数据和控制通信约束到 --tcp-network。普通 Pod 网络基线可使用 --network pod 并省略物理 --tcp-network，不能把主机网段套到 Pod IP 上。MPI Operator v0.6.0 管理密钥/hostfile/生命周期；端口由 Worker command 和 mpirun 参数配套设置，未依赖不存在的 MPIJob SSHPort 字段。

--bind-to none，CPU 由容器资源约束；严肃性能测试还需固定绑核/NUMA。结果中的 worker_pod_names 核对每个 rank 真正执行的 Worker Pod，同时保存 processor_names、实际节点和数值误差。

手工任务完成后释放端口：

~~~bash
kubectl delete mpijob rv-bcast -n rv-mpi --cascade=foreground --wait=true --timeout=120s
~~~

独立 MPI 用例每轮先保存日志/JSON，再清理已完成的 MPIJob、等待 Pod 消失后跑下一算法。KEEP_TEST_NAMESPACE=1 保留 namespace/失败现场，成功任务仍清理以释放端口。

## 8. Helm、Istio、Prometheus、Grafana

### 8.1 准备原则

这一部分是现有生态的 RISC-V 适配和集成。服务器保留 Go 1.21。选择与你当前 K8s、Go 1.21、openEuler 库兼容的固定版本源码并记录 commit。Istio 版本必须检查官方兼容表。

先查看所选源码的 go.mod 中 go/toolchain 声明。构建入口会设置 GOTOOLCHAIN=local，遇到要求更高 Go 的源码直接报告不兼容，不自动下载或安装编译器。可选处理方式：使用已经构建好的、匹配 K8s/版本/架构的 RISC-V 二进制或镜像；或者在另一台具备上游所需工具链的构建机完成构建，再将产物上传到镜像仓库/服务器。服务器本身始终可以保留 Go 1.21。不能仅修改第三方 go.mod 来跳过其真实编译要求。

来源：

- [Helm 源码](https://github.com/helm/helm)
- [Prometheus 构建说明](https://prometheus.io/docs/prometheus/latest/installation/)
- [Grafana 源码](https://github.com/grafana/grafana)
- [Istio 版本兼容](https://istio.io/latest/docs/releases/supported-releases/)
- [Istio 开发准备](https://github.com/istio/istio/wiki/Preparing-for-Development)

源码假设已分别检出到 /opt/src/helm、/opt/src/prometheus、/opt/src/grafana、/opt/src/istio、/opt/src/istio-proxy。前端需安装所选版本 package.json 指定的 Node.js/Yarn/npm，代理需匹配 Bazel/C++ 工具链。不是所有上游版本都原生支持 riscv64。

### 8.2 构建 Helm

~~~bash
bash platform/ecosystem/build-source.sh helm /opt/src/helm /opt/rv-build/helm
~~~

~~~bash
install -m 0755 /opt/rv-build/helm/helm /usr/local/bin/helm
~~~

~~~bash
helm version
~~~

### 8.3 构建 Prometheus

在 RISC-V Linux 原生构建，包含前端静态资源：

~~~bash
bash platform/ecosystem/build-source.sh prometheus /opt/src/prometheus /opt/rv-build/prometheus
~~~

~~~bash
docker build --build-arg BASE_IMAGE=YOUR_RISCV64_BASE_IMAGE -f platform/ecosystem/images/Prometheus.Dockerfile -t YOUR_REGISTRY/rv/prometheus:YOUR_VERSION /opt/rv-build/prometheus
~~~

~~~bash
docker push YOUR_REGISTRY/rv/prometheus:YOUR_VERSION
~~~

### 8.4 构建 Grafana

~~~bash
bash platform/ecosystem/build-source.sh grafana /opt/src/grafana /opt/rv-build/grafana
~~~

该步骤编译 Go 后端和前端。保留 public/conf；缺少前端文件即使后端进程启动也无法提供完整页面。Grafana 不同版本的 build.go 输出路径可能不同，脚本找不到 riscv64/grafana 时会停止。

~~~bash
docker build --build-arg BASE_IMAGE=YOUR_RISCV64_OPENEULER_IMAGE -f platform/ecosystem/images/Grafana.Dockerfile -t YOUR_REGISTRY/rv/grafana:YOUR_VERSION /opt/rv-build/grafana
~~~

~~~bash
docker push YOUR_REGISTRY/rv/grafana:YOUR_VERSION
~~~

### 8.5 构建 Istio

Go 控制面和 agent：

~~~bash
bash platform/ecosystem/build-source.sh istio-go /opt/src/istio /opt/rv-build/istio
~~~

匹配版本的 istio/proxy 需要支持 RISC-V 的 Bazel/C++ 工具链。依据所选 Istio 源码依赖锁选择对应 proxy commit，不能用版本无关的普通 Envoy 替代：

~~~bash
bash platform/ecosystem/build-proxy.sh /opt/src/istio-proxy /opt/rv-build/istio
~~~

若工具链/第三方依赖尚不支持 riscv64，会在这里失败。本项目提供构建入口和镜像集成，没有声称已经在你的机器完成所有 Envoy 依赖移植；这一项失败时 Istio 指标仍未完成。应保留构建日志，以具体错误继续适配，不能跳过然后写成功。

~~~bash
install -m 0755 /opt/rv-build/istio/istioctl /usr/local/bin/istioctl
~~~

~~~bash
docker build --build-arg BASE_IMAGE=YOUR_RISCV64_BASE_IMAGE -f platform/ecosystem/images/Pilot.Dockerfile -t YOUR_REGISTRY/rv-istio/pilot:YOUR_VERSION /opt/rv-build/istio
~~~

~~~bash
docker build --build-arg BASE_IMAGE=YOUR_RISCV64_OPENEULER_IMAGE -f platform/ecosystem/images/Proxy.Dockerfile -t YOUR_REGISTRY/rv-istio/proxyv2:YOUR_VERSION /opt/rv-build/istio
~~~

~~~bash
docker push YOUR_REGISTRY/rv-istio/pilot:YOUR_VERSION
~~~

~~~bash
docker push YOUR_REGISTRY/rv-istio/proxyv2:YOUR_VERSION
~~~

### 8.6 部署生态

~~~bash
cp platform/ecosystem/config.example.json /etc/rv-platform/ecosystem.json
~~~

~~~bash
vi /etc/rv-platform/ecosystem.json
~~~

填写 prometheusImage、grafanaImage、nodeTargets；Istio 填写 istioSource、istioHub、istioTag。istioHub 应是同时包含 pilot/proxyv2 的仓库前缀，tag 必须匹配源码 chart。enableIstio=true 表示完整安装；设为 false 只安装监控，报告不得宣称已经支持完整 Istio。

storage.enabled 默认 false，为开发用 emptyDir，Pod 重建可能丢监控历史/本地数据库。保存长期验收记录时提供实际 PVC/StorageClass，例如在 config 的 storage 中填写 enabled=true、storageClass、prometheusSize、grafanaSize。

~~~bash
bash platform/install.sh ecosystem /etc/rv-platform/ecosystem.json
~~~

安装器使用 skopeo 校验镜像 linux/riscv64、Helm lint、Helm --wait 和 rollout 健康门槛。服务启动成功还需要第 19 用例核对采集、仪表盘和实际 sidecar 注入。

~~~bash
kubectl get pods,services -n rv-monitoring -o wide
~~~

~~~bash
kubectl get pods -n istio-system -o wide
~~~

读取 Prometheus Service IP：

~~~bash
kubectl get service rv-prometheus -n rv-monitoring -o jsonpath='{.spec.clusterIP}'
~~~

把第 3 节 platform.json 的 prometheusURL 改成 http://该IP:9090，确保控制机网络能够访问该 Service。然后：

~~~bash
systemctl restart rv-platform-controller
~~~

若控制机没有 Service 路由，可先用转发做交互测试：

~~~bash
kubectl port-forward -n rv-monitoring service/rv-prometheus 9090:9090
~~~

该命令持续占用当前终端，停止后 127.0.0.1:9090 失效；长期控制器配置应使用稳定可达地址。

另开终端访问 Grafana：

~~~bash
kubectl port-forward -n rv-monitoring service/rv-grafana 3000:3000
~~~

浏览器打开 http://127.0.0.1:3000，用户名 admin。仅在自己的安全终端读取安装器生成的密码：

~~~bash
kubectl get secret rv-grafana-admin -n rv-monitoring -o jsonpath='{.data.password}' | base64 -d
~~~

Grafana 已预置 Prometheus 数据源与 RISC-V Platform 仪表盘，显示 CPU 和可用内存。

## 9. 场景感知、弹性和节点配置

### 9.1 场景策略

准备一个名为 web 的 Deployment，或使用现有需要测试的部署。它的 metadata 添加授权：

~~~bash
kubectl annotate deployment web platform.riscv.io/allow-scaling=web-scene --overwrite
~~~

~~~bash
kubectl apply -f platform/scenario/example.yaml
~~~

~~~bash
kubectl get scenariopolicy web-scene -o yaml
~~~

例子 query=max(rv_node_cpu_utilization_ratio)，高于 0.8 连续三次增加副本，低于 0.3 连续三次减少副本，动作间隔 90 秒，范围 1～8。按业务选择范围/查询表达式。查询必须返回唯一聚合序列。过期、多值、NaN 会拒绝动作，相同 scrape 时间戳不会重复计数。

不得与 HPA 或另一个策略同时控制同一 Deployment。ResourceClaim 管理的 Deployment 先把 externalScaling=true；直接管理的 Deployment 无需这项字段。授权注解值必须等于策略名。

### 9.2 按场景配置节点

在 NodeProfile metadata.annotations 添加 platform.riscv.io/allow-scenario: 策略名。场景动作可写：

~~~yaml
highAction:
  type: configureNode
  profile: worker1-profile
  labels:
    platform.riscv.io/pool: latency
  taints: []
  schedulable: true
~~~

场景控制器修改 NodeProfile，再由资源控制器应用到节点。配置冷却与双阈值，避免节点在两种场景间频繁切换。

### 9.3 按场景迁移

动作可写：

~~~yaml
highAction:
  type: migrate
  sourcePod: memory-counter
  targetNode: worker2
~~~

源 Pod 必须满足迁移组件的 opt-in/单容器/无卷等条件，且第 10 节节点运行时已经就绪。场景动作仅创建 NodeMigration，不绕过迁移验证。对象名由源 Pod UID 和目标节点确定，重复观测不会无限创建同一迁移；迁移失败需按状态处理。

### 9.4 独立 ResourcePolicy

适合对 Deployment 某个 PromQL 指标按目标值伸缩。字段包括 deployment/query/target/minReplicas/maxReplicas/cooldownSeconds/downscaleStabilizationSeconds。算法为 ceil(当前副本*当前值/目标值)，容忍区默认 10%，缩容需要稳定低值窗口。

设置 Deployment 的 allow-scaling=该 ResourcePolicy 名。不要再给该 Deployment 挂 ScenarioPolicy/HPA。这个控制器不是原生 HPA 的替代品；原生 HPA 的大纲用例仍用原生 HPA 测试。

## 10. 节点状态迁移

详细前置、升级 containerd、恢复故障和限制请完整阅读 migration/README.md。下面仅列集成顺序：

1. 每个源/目标节点具备 containerd 2.1.x（当前锁定验证 API 为 2.1.5）、runc、CRIU。
2. CRIU check 在真实 RISC-V 内核通过；空闲节点上安装 CRI 代理。
3. 迁移控制器配置节点 SSH 表和专用凭据。
4. 独立预检、计数工作负载、迁移、内存连续性测试分别执行。

编译：

~~~bash
bash platform/install.sh migration-build
~~~

每个 Worker 安装：

~~~bash
bash platform/install.sh migration-node
~~~

控制机：

~~~bash
cp platform/migration/config.example.json /etc/rv-platform/migration.json
~~~

~~~bash
vi /etc/rv-platform/migration.json
~~~

按实际节点填写 SSH；可复用 /root/.ssh/rv-platform，配置中 identityFile 必须一致。

~~~bash
bash platform/install.sh migration-controller /etc/rv-platform/migration.json
~~~

~~~bash
systemctl status rv-migration-controller --no-pager
~~~

控制面、节点 CRI 代理、containerd 的责任不同。源状态先停止再导出，目标由 kubelet 正常恢复；归档校验和 restored 标记通过后才删除源 Pod。RecoveryRequired 表示源可能已停止，需要人工按迁移手册恢复；不自动删除归档或伪装成功。

## 11. 独立测试与比较

### 11.1 通用准备

控制机：

~~~bash
export TEST_IMAGE=YOUR_REGISTRY/rv-communication:0.2.0
~~~

~~~bash
export MPI_IMAGE=YOUR_REGISTRY/rv-communication:0.2.0
~~~

~~~bash
export TEST_NODE=worker1
~~~

~~~bash
export TEST_RESULTS=/opt/rv-test-results
~~~

~~~bash
mkdir -p /opt/rv-test-results
~~~

~~~bash
bash platform/install.sh validation
~~~

测试默认创建独立 namespace 并清理，原始结果留在 TEST_RESULTS。KEEP_TEST_NAMESPACE=1 可保留现场。TEST_TIMEOUT 可调整等待时间。抢占测试只在无其他普通业务的专用节点上运行，需显式设置 ALLOW_PREEMPTION_TEST=1。

### 11.2 大纲 12 项，每条分别执行

~~~bash
bash platform/validation/01-hpa-expand.sh
~~~

~~~bash
bash platform/validation/02-hpa-shrink.sh
~~~

~~~bash
bash platform/validation/03-affinity.sh
~~~

~~~bash
bash platform/validation/04-node-exclusion.sh
~~~

~~~bash
export ALLOW_PREEMPTION_TEST=1
~~~

~~~bash
bash platform/validation/05-preemption.sh
~~~

~~~bash
bash platform/validation/06-taint-reject.sh
~~~

~~~bash
bash platform/validation/07-taint-tolerate.sh
~~~

多节点 MPI 测试前选择实际共同可达的 IPv4 物理网段（下面是示例）：

~~~bash
export MPI_NETWORK=host
~~~

~~~bash
export MPI_TCP_NETWORK=192.168.102.0/24
~~~

~~~bash
export MPI_SSH_PORT=25222
~~~

host 模式强制 Worker 按节点分散。单 Worker 设置 MPI_WORKERS=1、MPI_SLOTS=2，仅自检节点内功能；MPI_SPREAD=0 不能把多个同端口 host Worker 挤到同一节点。普通 Pod 基线用 MPI_NETWORK=pod，并在运行前 unset MPI_TCP_NETWORK。算法比较保持网络和其他参数一致。


~~~bash
export MPI_WORKERS=2
~~~

~~~bash
export MPI_SLOTS=2
~~~

~~~bash
export MPI_SPREAD=1
~~~

~~~bash
bash platform/validation/08-bcast.sh
~~~

~~~bash
bash platform/validation/09-reduce.sh
~~~

~~~bash
bash platform/validation/10-allgather.sh
~~~

~~~bash
bash platform/validation/11-fusion.sh
~~~

~~~bash
bash platform/validation/12-node-affinity.sh
~~~

03 使用真正 PodAffinity；04 保留大纲节点标签不匹配的拒绝测试。补充真正 PodAntiAffinity：

~~~bash
bash platform/validation/13-pod-antiaffinity.sh
~~~

原大纲对反亲和性的文字与示例有不一致，报告应明确 04 与 13 的区别，不能互相冒充。

### 11.3 资源、场景、隔离、设备

~~~bash
bash platform/validation/14-resource-pool.sh
~~~

~~~bash
bash platform/validation/15-scenario.sh
~~~

15 使用真实 Prometheus vector() 返回值做控制闭环注入；不是实际 CPU 负载的性能比较。

~~~bash
bash platform/validation/20-isolation.sh
~~~

20 在真实容器中解析 mountinfo/self cgroup 并检查 CPU=0.2 核、内存=64Mi 的有效内核限制，针对 cgroup v1。

真实设备测试：

~~~bash
export DEVICE_RESOURCE=hardware.riscv.io/accelerator
~~~

~~~bash
export DEVICE_CONTAINER_PATH=/dev/YOUR_REAL_DEVICE
~~~

~~~bash
bash platform/validation/21-device.sh
~~~

该用例检查设备映射、占用后的拒绝、释放后的再次分配，需要专用、未被其他业务占用的设备池。

### 11.4 软件网络对照

先安装第 6 节控制器，准备两个 Ready 的真实 Worker、可互通节点网络。脚本自动创建四端：普通 Pod 客户端/服务端、hostNetwork 客户端/服务端。两组固定物理节点对、实际镜像摘要、资源、端口、流数和时长，无需手动建 Pod 或额外配网卡/IP。

~~~bash
export TEST_IMAGE=YOUR_REGISTRY/rv-communication:0.2.0
~~~

~~~bash
export NETWORK_CLIENT_NODE=worker1
~~~

~~~bash
export NETWORK_SERVER_NODE=worker2
~~~

~~~bash
export NETWORK_PORT=25201
~~~

~~~bash
export NETWORK_REPEATS=3
~~~

~~~bash
bash platform/validation/16-network-host.sh
~~~

默认每模式三次、每次 30 秒、四条流，交替顺序。IPERF_SECONDS 可设 1～120，IPERF_STREAMS 可设 1～128。服务节点端口必须空闲；第 6 节示例占用同端口时先正常删除或改 NETWORK_PORT。

TEST_RESULTS 中保存 comparisonId、trial、节点/Pod UID/IP、实际镜像摘要、资源、路由及完整 iperf3 JSON。检查 host 模式 podIP=hostIP，且路径没有选到 flannel/cni/veth。吞吐使用接收端 sum_received。report.py 只对同轮次、同条件且完整的样本配对，hostOverPod 小于 1 照实保留。

只有一个 Worker 时拒绝跨节点对照，需要增加第二个 Worker，不能用同机回环冒充跨节点吞吐。测试正常删除专用 namespace；失联导致 finalizer 停留时恢复节点再清理。

### 11.5 高精度和 MPI 算法参数

可用 MPI_COUNT/MPI_REPEATS/MPI_CHUNK/MPI_WORK/MPI_ROOT 调整消息大小、重复、分块、计算工作量和非零根节点。建议遍历小/大消息和 root=0/最后一个 rank。不要只选一个对自己有利的点。

HAN 可用性先查看目标镜像：

~~~bash
ompi_info --param coll all
~~~

若包含 HAN，再设置：

~~~bash
export MPI_ARGS_JSON='["--mca","coll_han_priority","100"]'
~~~

重新运行 08～10，保留原始结果。report.py 按 mpiArgs 区分配置，不把不同基线参数混算。

### 11.6 时延/截止期

把 latency.py 放到真实服务节点或业务 Pod，在服务器运行：

~~~bash
python3 platform/validation/latency.py server --host 0.0.0.0 --port 19090
~~~

该命令持续运行。另一个真实节点客户端执行，下面 5ms 仅是填写方式示例，应替换为项目约定值：

~~~bash
bash platform/validation/17-latency.sh --host SERVER_REAL_IP --port 19090 --count 10000 --bytes 4096 --deadline-ms 5 --output /opt/rv-test-results/latency.json
~~~

输出保留全部样本、P99、最大值、违约数和数据校验结果。有违约时脚本退出非零。--fifo-priority 是可选 Linux SCHED_FIFO 参数，需要对应权限且应在专用测试环境使用；它不能单独形成硬实时保证。分别在空载和项目约定压力条件下运行，保留同等压力的基线。

### 11.7 规模

周期计算截止期另用独立 C 测试；与 TCP 时延分开。下面参数仅示例：10000 个周期、周期 1000us、截止期 1000us、每周期 10000 次 long double 乘加，未设置绑核/FIFO：

~~~bash
bash platform/validation/22-compute-realtime.sh 10000 1000 1000 10000 -1 -1 > /opt/rv-test-results/compute-realtime.json
~~~

可在末尾指定 CPU 编号和 SCHED_FIFO 优先级，RV_MLOCK=1 可要求锁内存；这些设置失败会停止。该测试使用绝对周期唤醒，记录启动抖动、完成时延、全部样本与违约数。有违约返回非零；这是指定计算负载的实测，不是硬实时保证。

真实节点数量和目标容器数由正式方案确定。先从可承载小规模开始：

~~~bash
bash platform/validation/18-scale.sh --image YOUR_REGISTRY/rv-communication:0.2.0 --pods 20 --clients 4 --output /opt/rv-test-results/scale-20.json
~~~

再按实际容量提高 --pods。不要在一台 Worker 上直接请求不可能承载的万级 Pod 并把创建请求数当成功数。脚本只有所有目标 Pod Ready 才 PASS，会记录实际运行节点、版本和每 Pod 就绪观测时间。

单独 KWOK 集群控制面测试：

~~~bash
bash platform/validation/18-scale.sh --image YOUR_REGISTRY/rv-communication:0.2.0 --pods 1000 --mode kwok-control-only --output /opt/rv-test-results/kwok-control.json
~~~

这条仅在你已经配置好的隔离 KWOK 集群执行，脚本不会自动把真实集群换成模拟集群，也不会把结果写成真实网络性能。

### 11.8 生态联调

设置控制机可访问的 PROMETHEUS_URL、GRAFANA_URL 和 GRAFANA_PASSWORD（或 GRAFANA_TOKEN）。测试需要 TEST_IMAGE 和 istioctl。

~~~bash
export PROMETHEUS_URL=http://PROMETHEUS_SERVICE_IP:9090
~~~

~~~bash
export GRAFANA_URL=http://127.0.0.1:3000
~~~

密码使用交互输入，避免写进命令历史：

~~~bash
read -rs -p 'Grafana password: ' GRAFANA_PASSWORD
~~~

~~~bash
export GRAFANA_PASSWORD
~~~

~~~bash
bash platform/validation/19-ecosystem.sh
~~~

~~~bash
unset GRAFANA_PASSWORD
~~~

测试检查节点 scrape 为 up、Grafana 数据库/仪表盘、真实 sidecar 注入及 istioctl proxy-status。

### 11.9 迁移四个独立步骤

逐步依照 migration/README.md 设置 SOURCE_NODE/TARGET_NODE/COUNTER_IMAGE 等变量。源码入口：

~~~bash
bash platform/migration/tests/01-preflight.sh /etc/rv-platform/migration.json
~~~

~~~bash
bash platform/migration/tests/02-create-counter.sh
~~~

~~~bash
bash platform/migration/tests/03-migrate.sh
~~~

~~~bash
bash platform/migration/tests/04-verify-memory.sh
~~~

### 11.10 生成比较报告

~~~bash
python3 platform/validation/report.py /opt/rv-test-results --output /opt/rv-test-results/comparison-report.md
~~~

无可配对数据时明确显示“无可配对实测数据”。speedup<1 表示本实现更慢，工具不会改写成正向结论。报告结合 comparison.md 的能力对照使用；未运行项目保持未实测。

## 12. 统一集成入口

节点、镜像和各配置准备完成后，控制机可顺序安装控制器、迁移控制器、生态：

~~~bash
bash platform/install.sh all /etc/rv-platform/platform.json /etc/rv-platform/migration.json /etc/rv-platform/ecosystem.json
~~~

这个入口不会替你伪造远端节点安装成功，也不会自动制作缺少的 RISC-V Envoy/设备驱动。遇到错误立即停止，处理具体错误后可以重跑安装步骤。

~~~bash
bash platform/install.sh check
~~~

check 检查 Node 架构/Ready、自定义 API 和 MPIJob CRD；它是就绪检查，不是项目全部验收。

## 13. 运维与故障定位

| 现象 | 先看哪里 | 处理 |
|---|---|---|
| ResourceClaim Error | CR status.message、控制服务日志 | 资源 request/limit、镜像、归属、节点选择 |
| Pod Pending | kubectl describe pod | 容量、污点、亲和性、设备、MPI_SPREAD 节点数 |
| 场景不动作 | status.message/lastSampleEpoch | Prometheus URL、查询唯一序列、时钟、冷却、授权、HPA 冲突 |
| 主机网络服务不可达 | Pod logs/describe、ss -ltnp、ip route get | 端口占用、IP/路由、防火墙或应用监听 |
| 主机网络删除停留 | HostNetworkWorkload finalizer、Pod、节点 Ready | 恢复节点，保持控制器运行，等待正常释放 |
| 设备容量 0 | device-plugin journal、真实 /dev 路径 | 修复驱动/清单/权限，不伪造健康 |
| MPI Pending/失败 | MPIJob conditions、launcher/worker logs | 镜像、SSH、节点数量、Open MPI 版本 |
| MPI correct=false | JSON 误差/精度 | 保留输入和日志，停止性能结论，检查 MPI/平台精度 |
| Grafana 空图 | Prometheus targets、节点9108 | nodeTargets、网络访问、采样状态 |
| Istio 构建失败 | Bazel/依赖/toolchain 日志 | 继续适配对应源码；此时指标仍待完成 |
| RecoveryRequired | NodeMigration status、节点归档 | 按 migration 手册恢复，禁止自动重复不确定检查点 |

常用日志：

~~~bash
journalctl -u rv-platform-controller -f
~~~

~~~bash
journalctl -u rv-migration-controller -f
~~~

~~~bash
journalctl -u rv-migration-proxy -n 100 --no-pager
~~~

~~~bash
journalctl -u rv-device-plugin -n 100 --no-pager
~~~

停止场景/资源/主机网络控制：

~~~bash
systemctl stop rv-platform-controller
~~~

停止控制器不会自动清理用户任务。删除 HostNetworkWorkload 前保持控制器运行，以正常释放 finalizer。迁移 CRI 代理卸载必须按 migration/scripts/disable-node.sh 恢复 kubelet endpoint；不要只停代理导致 kubelet失去运行时。

权限：当前自定义 CRD 面向集群管理员。控制器能创建工作负载、修改节点，SSH 节点工具具有 root 能力；不能直接把这些 API 当多租户不可信用户接口。控制机私钥/专用 kubeconfig 保持 0600，不放入镜像或测试报告。

## 14. 本地开发验证

~~~bash
bash platform/validation/00-local-unit.sh
~~~

~~~bash
bash platform/migration/tests/05-local-unit.sh
~~~

~~~bash
cd platform/device-plugin
~~~

~~~bash
go test ./...
~~~

这些是源码验证，不等于目标集群通过全部测试。具体本地验证记录见 VERIFICATION.md。

