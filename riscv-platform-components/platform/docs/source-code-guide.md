# 平台源码逐模块讲解

## 1. 阅读路线

建议顺序：

1. platform/install.sh：每个角色/组件安装入口。
2. platform/control/common.py：K8s 访问、归属、状态更新、指标规则。
3. resource.py：资源 HAL 和节点配置。
4. node/agent.py、device-plugin/main.go：节点采样与设备接入。
5. network.py：TCP 端口预留和主机网络 Pod 生命周期。
6. communication/rv_collective.c：实际通信算法与计算通信流水。
7. scenario.py：感知到动作。
8. migration/controller.py 和 migration/runtime/main.go：状态迁移。
9. ecosystem：现有生态适配。
10. validation：实际测试和比较报告。

K8s 内部组件、上游网络和 MPI 原理不在本项目重新实现。本项目新增的 API/控制和算法在这里解释。

## 2. 总体数据流

~~~mermaid
flowchart TB
    User[管理员配置] --> CR[K8s 自定义资源]
    CR --> Controller[平台控制器]
    Controller --> Deployment[Deployment / Node 配置]
    Deployment --> Scheduler[标准 K8s 调度器]
    Scheduler --> Kubelet[kubelet]
    Kubelet --> Runtime[containerd / runc / cgroup]
    CR --> HostNet[主机网络控制器]
    HostNet --> HostPod[hostNetwork Pod]
    HostPod --> Scheduler
    HostPod --> TCP[现有节点网络 TCP/IP]
    Device[通用设备插件] --> Kubelet
    Agent[节点 exporter] --> Prometheus
    Prometheus --> Scenario[场景控制器]
    Scenario --> CR
    MPI[MPIJob] --> Library[分层通信和融合库]
    CR --> Migration[独立迁移控制器与 CRI 代理]
~~~

控制逻辑修改期望状态，K8s 节点侧完成执行。CPU/内存不会绕过 K8s 记账直接在主机“多写一个数”。Pod 迁移则必须保存运行状态，所以另设运行时接口和恢复代理。

## 3. 目录和进程

| 目录/文件 | 运行位置 | 作用 |
|---|---|---|
| install.sh / integrate.sh | 控制机或节点，按子命令 | 安装入口和控制机顺序集成 |
| control/controller.py | 控制机 systemd | 资源、节点、场景、主机网络控制循环 |
| control/common.py | 控制机 | K8s JSON 操作 |
| node/agent.py | Worker systemd/SSH | /proc 指标、只读 HAL、NIC 前置检查 |
| device-plugin/main.go | Worker systemd | kubelet 设备插件服务 |
| migration/controller.py | 控制机 systemd | NodeMigration 状态机 |
| migration/runtime/main.go | Worker systemd/SSH | CRI 代理、状态导出/导入/恢复授权 |
| communication | MPI 容器进程 | 实际集合通信/融合算法 |
| ecosystem | 构建机/控制机 | 源码构建、镜像和 Helm 部署 |
| validation | 控制机/业务节点 | 独立用例、原始数据和报告 |

平台控制器与迁移控制器都是单个活动实例，由 systemd 重启。flock 防止同机重复启动；没有跨控制主机 leader election。不要在两台控制机上启动相同写入范围的控制器。

## 4. API 和持久状态

API 组 platform.riscv.io/v1alpha1，资源为 namespaced：

| Kind | 用途 | 主要 spec | 主要 status |
|---|---|---|---|
| ResourceClaim | 申请/调整运行资源 | image、command/args、replicas、requests/limits、nodeSelector/affinity/tolerations、externalScaling | workload、workloadUID、replicas、availableReplicas、phase |
| ResourcePolicy | 按目标指标弹性伸缩 | deployment、query、target、min/maxReplicas、cooldown/downscaleStabilization | value、replicas、desiredReplicas、lastScaleEpoch、lowSinceEpoch |
| NodeProfile | 节点期望配置 | node、labels、taints、schedulable | allocatable、managedLabels、phase |
| HostNetworkWorkload | 一个主机网络工作负载 | node/image/ports、requests/limits、command | pod/nodeIP/ports、phase/ready/mode |
| ScenarioPolicy | 场景反馈 | query、high/low、连续样本、冷却、highAction/lowAction | value、direction、consecutive、lastSampleEpoch、lastActionEpoch、lastAction |

迁移另使用 migration.riscv.io/v1alpha1 NodeMigration，避免将具有破坏性状态转换的迁移混入普通无状态副本控制。

manifests.py 输出 CRD 与专用 RBAC/ServiceAccount，使用 preserve-unknown-fields 保留 spec；运行时校验负责拒绝非法值。优点是首版 API 可扩展，限制是 API Server 不会提前验证全部业务字段。正式多租户 API 应补充完整 OpenAPI/CEL/准入校验。当前面向管理员配置。

status 使用单独子资源，写 observedGeneration 和 updatedAt。phase=Error 时 message 保存具体错误，不表示相关 Pod 已删除；对账可在修正配置后重新进行。

## 5. common.py：控制器共用基础

### 5.1 Kube.call

自定义资源请求始终加 platform.riscv.io 组名，避免与 K8s DRA 内置 ResourceClaim 重名。通过 subprocess.run 调用 kubectl，命令参数使用数组，JSON 写入 stdin。没有拼接 shell 命令。限制每次调用最长 90 秒，非零退出直接抛异常。missing=True 仅把 NotFound 转成 None，权限/连接错误仍报错。

这是适合当前 host/systemd 构建脚本的轻依赖实现，不是高性能 client-go informer。大规模控制器可换成 watch/informer，但不能把当前轮询吞吐未经测试就标为无限扩展。

### 5.2 乐观并发控制

Kube.patch 先使用 JSON Patch test 校验 metadata.uid 和 metadata.resourceVersion，再 add 指定字段：

- UID 检查阻止对同名重建对象误操作。
- resourceVersion 检查阻止把别人刚修改的对象覆盖掉。
- 发生冲突会在下一轮重新读取、重新计算。

一个 patch 里的 test 和修改由 API Server 原子处理。不能把客户端先 get 后无条件 patch 当成同样保证。

### 5.3 ownerReference 与标签

platform.riscv.io/owner 标签保存父 CR UID。控制器创建子 Deployment/Pod 时带 ownerReference。遇到同名对象时先检查归属，不接管别人创建的资源。

ResourceClaim 删除由 K8s GC 回收；HostNetworkWorkload 的 finalizer 等待正常 Pod 删除与节点 Ready 后释放端口预留。

### 5.4 quantity 和指标规则

quantity 把 CPU/memory 数量换成 Decimal，避免用二进制 float 比较 Kubernetes 资源额度。支持 m、Ki/Mi/Gi 等实际使用单位；不接受负资源。

metric_sample 要求：

1. 恰好一个 Prometheus vector 元素。
2. 时间戳没有过期，也不能明显来自未来。
3. value 是有限数值。

多个节点的值应先在 PromQL 中聚合或筛选，不能让控制器随便取第一条。

## 6. resource.py：资源池 HAL

### 6.1 resources

requests 与 limits 都必须给出，每一项都成对存在且 0<request<=limit。

CPU、memory、ephemeral-storage 可按普通 Kubernetes 资源定义。带域名的扩展资源必须等 request/limit 且为整数，例如硬件加速器数量 1。没有把设备数量当可压缩 CPU 那样超卖。

### 6.2 KubernetesBackend 接口

- describe：按 Claim 名查 Deployment。
- desired：把管理员声明转换成目标 Deployment。
- allocate：创建或对账已有 Deployment。
- resize：使用相同对账路径落实新资源/副本/节点选择。
- release：说明通过 ownerReference/GC 回收。

desired 保留 command/args/env/ports、节点选择、affinity/tolerations、runtimeClass/imagePullSecrets，拒绝 nodeName 直接指定。nodeSelector 仍必须经过调度器，才能保证请求被记账并满足容量、污点等约束。

默认关闭 ServiceAccount token 挂载，seccomp 使用 RuntimeDefault。是否为具体应用开放其他权限需另行配置，首版 Claim 不是任意 Pod YAML 透传。

### 6.3 扩缩和资源调整的区别

replicas 改变：扩大/缩小部署资源总量。

requests/limits 改变：修改 Pod template，Deployment 滚动创建替代 Pod。容器有效 cgroup 由 kubelet/runtime 设置，过程可能重启应用。

nodeSelector 改变：新 Pod 按新的节点池部署。这适用于无状态滚动迁移，不保存旧进程内存。需要内存状态时走 NodeMigration 的不同路径。

externalScaling=true 时，资源控制器保留现有 Deployment replicas。否则每轮都会按 Claim 的固定副本对账，这会与外部 HPA/场景控制器冲突。

### 6.4 Resources.claim

把 Deployment 的 availableReplicas 与目标 replicas 比较，目标全部可用时 phase=Ready，否则 Allocating。写回底层对象 UID 和实际可用数。该状态不会伪装为设备业务测试通过。

### 6.5 Resources.profile

- 一个节点只允许一个 NodeProfile。
- 只管理 platform.riscv.io/ 前缀标签和污点。
- 保留外部标签/污点。
- taint 只允许 NoSchedule/PreferNoSchedule，避免用配置更新意外驱逐已有 Pod。
- schedulable 转换成 Node.spec.unschedulable。
- 返回节点真实 allocatable。

managedLabels 用于下次删除本配置以前管理但现在移除的标签。NodeProfile 删除不会自动撤销节点状态，这一点在安装手册明确说明。

### 6.6 “跨边界”的实现边界

此后端把多个节点作为资源池，通过调度、部署、副本伸缩和状态迁移实现跨节点资源流动。它没有把远端 RAM 变成本地 RAM；没有设备驱动支持时也不会把远端设备变成透明本地设备。更强的远端借用语义需接入对应传输和硬件后端。

## 7. node/agent.py：动态采样

### 7.1 CPU

读取 /proc/stat 第一行的 user/nice/system/idle/iowait/irq/softirq/steal 累计计数，排除已计入 user/nice 的 guest 计数。

相邻采样：

utilization = 1 - Δ(idle+iowait)/Δ(total)

采样线程每秒更新一次，与 HTTP scrape 周期分离。不能把“某一次 CPU 累计值”直接当利用率。

### 7.2 内存和其他指标

/proc/meminfo 的 kB 换成 byte，输出总内存、MemAvailable；/proc/loadavg 输出 load1；同时记录 CPU 数、cgroup 版本和采样时间戳。

HTTP 仅提供 /metrics。首次样本还没有形成时返回 503，避免拿未初始化的零值触发策略。指标带 rv_node_ 前缀，Prometheus 的 instance 区分节点。

### 7.3 describe

返回架构、内核、CPU、内存、cgroup 版本及后端；接口列表只是只读系统信息，不作为单独验收指标或网络方案的硬件条件。旧 check-network 已删除。网络控制器只用 K8s API，SSH 留给迁移等管理操作。

## 8. device-plugin/main.go：异构硬件接入

### 8.1 管理员清单

每条 Device 指定 id、hostPath、containerPath、permissions 和可选 NUMA。hostPath/containerPath 必须是规范的 /dev 路径，禁止路径穿越和重复 ID/主机路径。

healthy 通过 os.Stat 检查真实字符/块设备。这个判断只能证明设备节点存在；深度设备自检需具体驱动后端扩展，不能据此宣称加速器计算正确。

### 8.2 注册过程

1. 在 kubelet device-plugins 目录创建本插件 Unix socket。
2. 启动 DevicePlugin gRPC 服务。
3. 连接 kubelet.sock 的 Registration API。
4. 注册扩展 resourceName 与 endpoint。
5. kubelet 通过 ListAndWatch 获取设备及健康。
6. 调度器基于 Node allocatable 给请求该资源的 Pod 放置。

kubelet 重启删除插件 socket 后，进程退出，由 systemd 重启并重新注册。当前默认安装器一类资源一个服务，避免多个资源复用同一个 endpoint。

### 8.3 Allocate

kubelet传入设备 ID，插件核对：

- ID 在管理员清单中。
- 设备当前健康。
- 同一分配请求没有重复占用同一 ID。

返回 HostPath/ContainerPath/Permissions 的 DeviceSpec，让运行时真正映射设备。独占分配和释放记账由 kubelet 完成；插件没有另造一份容易与 kubelet 不一致的资源余量。

NUMA 信息作为 TopologyInfo 提供。是否实行 CPU/设备同 NUMA 约束还取决于 kubelet Topology Manager/CPU Manager 配置，不是提供一个数字就自动绑定。

### 8.4 不实现的驱动语义

没有虚构 GPU/RDMA/FPGA 驱动、硬件计算算子或设备状态迁移。对实际设备进行计算需要应用/驱动，设备迁移需要对应状态导出/恢复协议。本模块把已经可用的设备接到 Kubernetes 调度和容器中。

## 9. network.py：软件主机网络组件

### 9.1 API 与资源模型

HostNetworkWorkload 描述 node/image/ports、command/args/env、requests/limits，以及可选 tolerations/imagePullSecrets。一个 CR 对应一个 hostNetwork Pod，地址沿用节点。ports 为空表示只发起连接的客户端。

K8s 已有 hostNetwork/hostPort/nodeAffinity；新增代码组合这些能力，提供参数检查、跨 namespace 端口预留、所有权、不可变 spec、finalizer 和可复现实测。底层网络由 K8s/Linux 提供，不是自行研发的物理直通驱动。

### 9.2 ports(spec)

验证 containerPort 是 1024～65535 的整数，拒绝 bool（Python 的 bool 是 int 子类）。仅接受 TCP，去重，hostPort 必须等于 containerPort，hostIP 固定 0.0.0.0，避免通过地址写法绕开互斥检查。返回标准 Pod ports，供原生 scheduler 检查 hostPort 冲突。

监听端口必须如实声明。账本覆盖本组件以及 K8s 声明的 hostPort，不自动覆盖宿主进程或漏报端口。应用绑定失败后应查看日志、换端口重建，不会停止主机服务来腾端口。

### 9.3 workload_pod(obj)

1. 拒绝旧 device/mac/address/gateway，避免误以为还分配网卡。
2. 要求 node/image，调用 resources 验证请求/限制。
3. 用父 UID 前缀生成 Pod 名，设置 ownerReference/UID 标签。
4. hostNetwork=true、dnsPolicy=ClusterFirstWithHostNet。
5. nodeAffinity 使用 metadata.name matchFields；不写 nodeName，由 scheduler 检查容量、污点和端口。
6. restartPolicy=Never，关闭服务账号自动挂载和 Istio 注入。
7. 完整排序 spec 存入 annotation，用于拒绝运行中原地修改。

镜像、命令、端口或节点调整时正常删除后重建。CPU/内存限制仍由 kubelet/cgroup 执行。

### 9.4 Networks.step

没有 Pod 时先补 finalizer，下一轮验证节点 Ready/可调度，再遍历所有 namespace 的 HostNetworkWorkload，拒绝同节点的重叠端口，创建 Pod 并写 Allocating。控制器单活动实例；scheduler 再对声明的 hostPort 做调度互斥。

已有 Pod 时检查 UID 归属和 spec annotation，之后回写 phase/ready/pod/node/nodeIP/ports/mode。Ready 仅表示容器可运行，任意业务是否开始监听需要应用探测，所以网络测试另做真实 TCP 连通检查。错误交由 controller.py 写 phase=Error/message。

进程退出后 CR/Pod 继续保留供查看日志，直到管理员删除任务。无监听端口的客户端可以共享节点。

### 9.5 删除、重启和数据路径

CR 删除后，先按子 Pod UID 正常删除 Pod、写 Releasing；Pod 消失且节点 Ready 后才释放 finalizer。失联节点保留预留，恢复后继续。重启从 K8s 对象恢复，无内存预留表；外部强删 Pod/finalizer 会破坏保证。

数据路径：容器进程 → 主机网络命名空间 TCP/IP → 现有网口 → 对端节点。普通 Pod 基线经 veth/CNI/Flannel。测试核对实际模式、IP 和路由，避免只改名字就声称绕过 overlay。

hostNetwork 网络隔离弱于普通 Pod，不能默认原 NetworkPolicy 同样适用，只在可信专用 namespace 使用。本模块不创建 NAD、不修改 CNI/网卡/路由，不依赖 RDMA/SR-IOV，也没有硬件卸载能力。

## 10. communication：分层通信算法

### 10.1 rv_context

world：整个 MPIJob 的 rank。

local：MPI_Comm_split_type(MPI_COMM_TYPE_SHARED) 形成的可共享内存分组。

leaders：每个 local 组 local_rank=0 的 leader。

leader_for_rank：所有 world rank 对应的 leader world rank。leader communicator 按 world rank 排序，便于找到任意 root 对应 leader 的序号。

程序 JSON 中 nodes 是 shared communicator 组数。物理节点数量以 Kubernetes workerNodes 和实际 placement 为准，不能仅凭共享组数认定实际服务器数。

### 10.2 广播 rv_bcast

支持任意 root，不只 rank0。

假设 rank0/1 在节点A，rank2/3 在节点B，root=3：

1. B 节点内部从 rank3 广播到 B leader=2。
2. leaders 之间从 leader2 广播到 leader0。
3. 每个节点从本地 leader 向 local 其他 rank 广播。

如果直接让 leaders 从一个不属于 leader communicator 的 world rank3 广播，会错误；root_leader 负责映射。

### 10.3 规约 rv_reduce_sum

每个 rank 提供 long double 数组：

1. 节点内 MPI_Reduce 到 leader。
2. leaders 规约到 root 所属节点的 leader。
3. root 所属 local 组广播最终数组，真正的 root 拷贝结果到 out。

规约重排浮点求和顺序，数值可能与 native 不逐 bit 相同。因此 benchmark 使用与规模/精度相关的相对误差，而不是把所有舍入差异当错误，也不会无限放宽误差掩盖问题。

### 10.4 全收集 rv_allgather

难点：不同节点 rank 数可不同，且 world rank 不一定连续按节点排列。

1. 各节点 Gather 本地 world rank 列表和数据。
2. leaders Allgather 每组 rank 数，形成 counts/offsets。
3. leaders 用 Allgatherv 汇集 rank IDs 和数据。
4. 按汇集的 world rank 列表，把数据散写到最终 world rank 顺序。
5. 每个 leader 向 local 广播完整结果。

如果只把各 leader 的数组拼接，会在不连续 rank 映射时返回错误顺序。本实现显式还原顺序，并检查 int count 乘法上界。

### 10.5 高精度

使用 MPI_LONG_DOUBLE，避免将高精度 payload 先转换成 float/double。运行时检查各 rank LDBL_MANT_DIG 一致；异构端精度不同直接失败。MPI datatype 负责表示传输，不直接把宿主 struct 内存强行跨架构发送。

这覆盖具有一致 long double 精度的实际集群。混合 ABI 精度不同的无损数值互操作，需要统一高精度格式/运算库另行实现，不能在这里静默降精度。标准 RISC-V ABI 的具体行为仍由实际编译器输出记录。

## 11. 计算通信融合

### 11.1 计算算子

rv_compute 对每个元素做固定次数的乘加变换，输入/工作量由参数固定。serial 和 overlap 使用相同算子，不用空循环当重计算，也不以无关计算掩盖丢失的归约结果。

### 11.2 serial

先计算整个数组，再执行阻塞 MPI_Allreduce。这个结果也用于融合实现的数值参考。

### 11.3 rv_fused_sum

数组按 chunk 切分，最多两个请求窗口：

1. 等待要复用的 request slot 完成。
2. 计算当前 chunk，写入自己独立的 send 区域。
3. 对此 chunk 提交 MPI_Iallreduce，out 区域也独立。
4. 下一 chunk 的计算可与前一 chunk 通信重叠。
5. MPI_Test 推进/检查另一请求。
6. 所有 chunk 提交后 Waitall，再释放 send 数组。

MPI_Iallreduce 返回并不代表发送已完成。发送缓冲和接收缓冲在对应 request 完成前都不能被其他算子覆盖。本实现保留整个 send 数组，分块区域不重叠，最后才释放。

真实 overlap 依赖 MPI/网络异步进展和计算/通信比例；小消息分块可能增加延迟，软浮点高精度可能计算占比极高。代码不保证任意硬件都加速。

### 11.4 benchmark.c

- 验证 operation/mode/count/root。
- 生成确定性输入。
- 用 native 集合通信生成参考结果。
- 先预热一次，再执行 repeats 次。
- Barrier 放在计时外，耗时取所有 rank 的最大值。
- 检查所有有效输出，检测 NaN/Inf。
- 输出相对误差、容忍度、实际精度、均值和最优耗时。
- correct=false 返回非零退出。

reduction 的误差基准为 native；它不能替代所有数学高精度证明。若正式业务有更严格误差上限，需要在该业务输入上额外验证。

### 11.5 job.py / launch.py / mpi_case.py

job.py 生成 v2beta1 MPIJob。host 模式要求物理 IPv4 tcp_network；Worker/Launcher 设置 hostNetwork/ClusterFirstWithHostNet。Worker sshd 在可配置 25222 端口监听，声明 hostPort，TCP readinessProbe 检查监听；按节点反亲和，Launcher 等 Worker Ready，再从 /etc/mpi/hostfile 正常 SSH 启动 rank。

端口由 Worker command 的 -p 和 mpirun plm_rsh_args 配套设置，不依赖不存在的 MPIJob SSHPort 字段。同端口多个任务串行或换端口。Downward API 注入 RV_WORKER_POD，sshd SetEnv 使远程 rank 得到它，benchmark 按 rank 收集 worker_pod_names。测试核对 Pod 名集合、每 Worker rank 数、总 rank 数及实际 nodeName；MPI_Get_processor_name 留作诊断，不靠猜 hostname 判断执行位置。

launch.py 查询 ompi_info，选择 ob1 + self,tcp，并保留可用 sm（新版本）或 vader（旧版本）节点内共享内存。跨节点 TCP，节点内不刻意关闭共享内存。btl_tcp_if_include 约束数据，OMPI/PRTE oob_tcp_if_include 约束控制通信。MPI_ARGS_JSON 可调 collective，拒绝覆盖传输/SSH 配置。

pod 模式是普通网络基线，不带 hostPort，也不应把物理网段指定为 Pod 接口。结果存 networkMode、tcpNetwork、transport、imageID，报告不混算不同条件。

成功一轮先存日志/JSON，前台级联删除 MPIJob，等 Pod 消失释放端口后再启动下一算法。KEEP_TEST_NAMESPACE=1 保留失败现场，但成功任务仍正常清理。

## 12. scenario.py：动态感知

### 12.1 query

访问 Prometheus HTTP query API，要求成功且为 vector。查询失败不会继续执行资源动作。

### 12.2 decide

双阈值：

- value>=high：high。
- value<=low：low。
- 其余：hold，清空连续计数。

同方向连续样本达到 consecutiveSamples，且距离 lastActionEpoch 达到 cooldownSeconds，才允许动作。low 必须小于 high。

lastSampleEpoch 用于去重。控制器轮询三次但 Prometheus 只有一个新 scrape，不能算三个连续新样本。status 持久化后重启仍保留冷却和计数。

扩缩前先把绝对目标副本数和 Deployment UID 写入 status.pendingScale，再修改 Deployment，最后提交动作状态。进程在修改成功、状态提交前中断时，重启只重放同一个绝对目标，不会把 delta 再加一次。恢复时再次核对 UID 和授权，避免同名重建对象被误改。错误回写读取最新 status，保留已持久化的动作意图。

### 12.3 scale 动作

Deployment 必须 annotation allow-scaling=策略名。检查原生 HPA、ResourcePolicy、其他 ScenarioPolicy 是否也控制该部署。动作支持固定 replicas 或 delta，最后限制在 min/max 内。patch 带 UID/resourceVersion 检查。

为什么需要互斥：如果 HPA 要 2、场景要 4，两个循环会互相覆盖，造成抖动和错误性能结论。单纯设置 cooldown 不能解决两个独立写入者的冲突。

### 12.4 migrate 动作

以场景 UID + 源 Pod UID + targetNode 形成确定的 NodeMigration 名。重复观测读取同一对象，不无限重复创建。迁移细节和安全条件由迁移控制器验证。

### 12.5 configureNode 动作

NodeProfile 必须显式 allow-scenario。动作只可更新 labels/taints/schedulable，不能改变其 node 身份。资源控制器进一步校验自己的标签前缀及污点效果。

### 12.6 Autoscaler / ResourcePolicy

按 current*sample/target 的向上取整值计算副本，范围有上下界，比例偏差在 10% 内不动作。缩容保留 lowSinceEpoch，在低需求持续窗口结束后才缩；同时保留动作冷却。与 HPA/ScenarioPolicy 冲突会拒绝。

这是本项目简化目标指标策略；原生 HPA 的多指标/缺失 Pod 指标保守计算等完整语义没有在这里重新实现。

## 13. 迁移代码

完整限制和恢复步骤见 migration/README.md。这里解释实现关键点。

### 13.1 Python 状态机

Pending → Validating → Checkpointing → Transferring → Restoring → WaitingForRestore → Committing → Succeeded。

所有阶段写在 NodeMigration.status，控制器重启后重新读取阶段，而不是把一次迁移藏在不可恢复的长 shell 会话里。

Validating 检查源 Pod opt-in、单容器、无 controller owner、restartPolicy、卷/设备/探针等限制；检查目标架构/内核/cgroup/runtime、节点 Ready、容量和镜像。校验不通过时不停止源任务。

### 13.2 为什么不能只修改 nodeName

Pod nodeName 不可随意重绑，修改名字也不会把内存和文件描述符迁到新节点。删除重建只会从镜像重新开始。迁移必须有检查点与恢复证明。

### 13.3 native checkpoint

迁移工具用 Go 1.21 可构建的 containerd SDK 1.7.27，通过稳定的 gRPC API 调用实际 containerd 2.1.5 daemon 的 Task.Checkpoint，Exit=true，先停止源执行，再导出内存状态和可写层。SDK 版本与 daemon 版本分别固定，不把 daemon 降为 1.7。元数据库改为 checkpointctl 1.1.0；status.dump 文件名用本地常量补齐，归档内容和目标恢复协议保留。普通 CRI CheckpointContainer 是取证接口，默认不等于完成跨节点迁移。

归档包含 containerd/CRIU 恢复所需内容与身份清单。SHA256/大小校验成功后才发布正式归档。保留 containerd 原检查点内容，导出失败不删除唯一恢复状态。

### 13.4 流式传输

控制器通过 SSH 将源 export 的 stdout 传到目标 import stdin。控制机不落盘检查点。SSH 使用已验证 host key 和指定私钥，传输内容本身再校验 SHA256。

import 检查成员路径/身份/必要文件，拒绝路径穿越和截断。先写临时文件，完成校验再原子替换，损坏传输不会覆盖已有有效归档。

### 13.5 CRI 代理授权

kubelet 仍通过标准 CRI 创建目标容器。代理平时原样转发 CRI 请求，只对已经授权的 namespace/Pod name/精确 UID/container/attempt/归档校验匹配请求，把 image 字段改成目标本地 checkpoint tar。

目标 Pod 先使用保留 schedulerName 创建，以获得 UID；安装 grant 后再 Binding 到目标 Node。这样不会因为“同名 Pod”碰巧出现就把状态交给它。

### 13.6 成功证明与源删除

WaitingForRestore 检查目标运行且 containerd 元数据 restored=true。Committing 再检查目标 UID/运行状态，按源 UID 条件删除源 Pod，并移除目标的迁移 ownerReference，避免删迁移记录顺便删业务。

这些证明运行时确实恢复，但应用状态连续性还要由独立计数测试校验。不能只看到目标 Running 就说保留了内存。

### 13.7 失败

停止源之前的明确失败可标 Failed。停止源之后的不确定失败标 RecoveryRequired，保留锁/finalizer/检查点。不盲目再执行检查点、不自动启动两份源、不删除档案。

停顿式有状态迁移是已确认的最终方案，预拷贝和零停顿不列为必做项。当前支持范围不包括任意已有 TCP、任意卷和设备状态迁移，也没有跨控制主机 leader election；这些范围限制与是否允许停顿分别说明。

## 14. ecosystem：构建和集成

go-env.sh 对本项目构建入口设置 GOTOOLCHAIN=local/GOWORK=off，最低 Go 1.21，禁止隐式下载新版工具链。迁移/设备模块的直接和间接依赖由 Go 1.21.0 重新 tidy 并锁定，构建使用 -mod=readonly。

build-containerd.sh 默认校验附带 RISC-V 产物，不调用 Go。CONTAINERD_BUILD_FROM_SOURCE=1 才启用另一台构建机的上游源码重建入口；containerd 2.1.5 自身要求 Go 1.23+。服务器可保留 Go 1.21。

build-source.sh 使用所选源码实际入口构建 Helm、Prometheus、Grafana、Istio Go 组件，并记录 source commit，同时禁止工具链自动升级。上游版本要求更高 Go 时，应使用匹配的预编译产物或在另一台构建机完成，而不是修改上游版本声明冒充兼容。Prometheus/Grafana 包含前端资源。

build-proxy.sh 运行匹配 istio/proxy 的 Bazel Envoy target，需要真实 RISC-V C++ 工具链。构建失败不会用一个 Go agent 冒充 Envoy，也不会自动跳过。

images 目录将这些产物放入 RISC-V 基础镜像。源码/系统依赖差异可能需要目标环境适配；本地没有完成 Envoy 全依赖移植与真实镜像运行。

install.py：

1. skopeo 审核 linux/riscv64 和 digest。
2. 创建监控 namespace。
3. 只在首次创建 Grafana 随机管理员密码 Secret。
4. Helm lint 和 --wait。
5. 使用匹配源码 chart 部署 Istio base/istiod。
6. rollout 健康检查。
7. 输出安装镜像记录。

chart 设置节点静态 scrape targets，预置 Grafana 数据源和 CPU/内存仪表盘。默认 emptyDir，长期数据需开启 PVC。服务均为 ClusterIP，由操作者决定访问方式。

## 15. validation：测试与比较

### 15.1 cases.py

每个 shell 入口只调用一个用例。共用 namespace/等待/记录/清理函数，不把所有测试合成“一键全跑”。

- HPA 测试实际增加/释放内存并观察原生 HPA。
- PodAffinity 用 anchor Pod 验证同节点。
- 节点标签排除检查 Unschedulable。
- 抢占测试使用相对负优先级，减少影响正常系统 Pod；要求专用节点。
- 污点测试限制到目标节点，避免未容忍 Pod 逃到其他节点造成假通过。
- NodeAffinity 检查真实 nodeName。
- ResourceClaim 更新检查新 Pod UID 与新资源限制。

### 15.2 isolation_case.py

从容器 /proc/self/cgroup 与 /proc/self/mountinfo 推导实际 controller 路径。读取 cpu.cfs_quota_us/cpu.cfs_period_us/memory.limit_in_bytes，比对期望。证明限制进入内核，而不仅是 YAML 写了 limits。

### 15.3 device_case.py

一个 Pod 占用一个设备，然后另一个请求完整池，预期无法分配。删除第一个，等待后一个成功。检验映射/独占/释放；不宣称验证了设备内部计算性能。

### 15.4 network_compare.py

在两个真实 riscv64 Worker 上自动建立普通 Pod 与 hostNetwork 两组四端。核对 Ready、nodeName、实际网络模式、IPv4、host 模式 podIP=hostIP 以及四端相同 imageID。固定资源/端口/流数/时长，TCP 连通探测后交替测试，保存路由和完整 iperf3 JSON。

接收端 sum_received 为吞吐，记录 comparisonId/trial、Pod UID、节点/地址、实际摘要和资源。host 路径选到 flannel/cni/veth 时拒绝；单节点不能输出跨节点吞吐。finally 正常删除 namespace，遵守 finalizer 预留语义。

### 15.5 latency.py

长度前缀 TCP echo，防止大 payload 互相阻塞；先预热，再测每次 round trip。发送确定性字节，逐次 SHA256 校验，保存所有时延。P99 使用 nearest-rank 统计，明确截止期违约次数。

SCHED_FIFO 为可选实验参数。该工具记录结果，不替普通 Linux/K8s提供硬实时数学保证。

### 15.6 scale.py

realtime.c 另测实际周期计算：clock_nanosleep 使用 CLOCK_MONOTONIC/TIMER_ABSTIME，避免相对 sleep 的周期累积误差；可配置 CPU affinity、SCHED_FIFO、mlockall。记录每周期实际启动偏差和完成时间，deadline_misses 根据完成时刻与计划释放时刻的差计算。固定 long double 工作量只代表此测试负载，不能替代真实业务的最坏执行时间证明。

并发提交实际 Pod，轮询所有 Pod 的 Ready，记录实际执行节点与观测就绪时间。real 模式拒绝明显 KWOK 节点，并约束 riscv64；kwok-control-only 明确标为控制面结果。create 请求成功不等于 Pod Ready，更不等于网络吞吐达标。

### 15.7 report.py

按 operation/limited/settings/workerNodes/image/imageID/mpiArgs/mantissa_bits/networkMode/tcpNetwork/transport 分组，仅比较 correct=true 且来自 real-MPIJob 的数据。

speedup = baseline_mean_seconds / candidate_mean_seconds。

网络按 comparisonId、节点对、镜像/实际摘要、资源、时长、流数、端口分组；pod/host 必须有完整一致、无重复的 trial 集合、真实证据、正确网络标记及正有限吞吐。缺半组不计算；hostOverPod=host_mean_bps/pod_mean_bps，低于 1 照实记录。

没有匹配基线、节点位置不同、参数不一致或错误结果时不计算“提升”。原始日志应一并保留。规模与截止期结果单独列出，不自动替项目设置验收阈值。

## 16. 扩展一个新后端/动作的步骤

### 新设备

先让驱动在宿主正常工作；在设备插件中扩展健康判断/必要 Allocate 返回项；提供实际应用的正确性测试。不要只增加一个资源名字就说支持了新硬件。

### 新资源后端

实现 Describe/Allocate/Resize/Release，解释调度记账、失败与回收、所有权、控制器重启后的幂等性；补充测试后再开放 backend 名。当前资源 backend 固定为 kubernetes，避免配置一个未实现名字仍显示 Ready。

### 新场景动作

在 Scenarios.step 添加动作，要求明确 opt-in、作用对象身份/版本检查、可观测结果和失败路径。需要异步执行的动作应创建独立 CR，并记录其状态，不在场景循环里一次性执行不可恢复长操作。

### 迁移范围约定

本次迁移方案固定为停顿式有状态迁移，不再增加预拷贝或零停顿优化。后续部署验证围绕当前实现进行：检查点保存、归档传输、目标恢复、进程内存状态延续、容器生命周期和失败处理。实际暂停时间记录在测试结果中。卷、已有 TCP 连接和设备状态仍遵守迁移手册的支持范围。

## 17. 本次代码验证应怎样理解

本地验证覆盖控制逻辑、归档/身份/并发保护、Bash/Python/YAML、Go 交叉编译、MPI C 编译和 Helm 渲染。目标平台尚未执行容器迁移、MPI 多节点、hostNetwork 多节点吞吐或生态镜像启动。验证记录见 VERIFICATION.md，功能覆盖和可比较差异见 comparison.md。

