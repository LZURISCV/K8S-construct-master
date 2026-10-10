# 现有能力、本项目新增代码与验证方式

## 1. 比较口径

本项目在用户提供的 K8S-construct-master 上增加组件。K8s、containerd/runc、CRIU、CNI、Open MPI、Helm、Istio、Prometheus、Grafana 的原有机制仍归各上游项目。下表中的“新增”指本目录实际新增的控制、适配、算法和测试代码，不自动等同于学术创新，也不代表已经在 RISC-V 集群验收。

截至本次交付：有可执行源码、部署脚本和独立测试；已完成本地测试及交叉编译检查。真实 RISC-V 集群、节点网络、CRIU 内核能力、Istio 代理和性能结果仍须在目标环境验证。没有生成虚构的提升百分比。

## 2. 原脚本与新增组件逐项对照

| 指标 | 原脚本/已有组件能做什么 | 本项目实际增加什么 | 用什么验证 |
|---|---|---|---|
| 1(1) RISC-V 容器方案 | dnf 安装 K8s/containerd；若干 RISC-V 镜像引用 | 多节点凭据签发、版本/架构检查、角色安装入口、详细手册 | check.py、节点 describe、镜像架构审核 |
| 1(2) 分布式、弹性、高扩展 | 标准 API Server/controller-manager/scheduler；原 HPA 测试材料 | ResourceClaim、ResourcePolicy、独立节点执行、控制器可配置节点表、规模测试 | 01/02、14、18 |
| 1(3) 云原生生态 | Flannel、metrics-server、CoreDNS、MPI Operator 入口；未装完整生态 | Helm 源码构建、Prometheus/Grafana chart 和仪表盘、Istio 源码/镜像构建与 Helm 安装入口 | 19、镜像审计、注入和健康检查；Envoy 需真实 RISC-V 构建 |
| 2(1) 资源抽象和跨边界伸缩 | K8s requests/limits、调度、多副本 | HAL Describe/Allocate/Resize/Release；ResourceClaim 跨节点部署/扩副本；NodeProfile 配置；设备插件接入 | 14、修改 nodeSelector/replicas 的滚动更新、设备独占测试 |
| 2(2) 轻量细粒度隔离 | namespace、runc、cgroup | 统一 CPU/内存/扩展资源请求与限制；cgroup v1 配置；不绕过 kubelet 私改记账 | 20-isolation、设备限额、节点实际 cgroup |
| 2(3) 在线迁移/跨节点流动 | 标准 K8s 重建 Pod 不保存进程内存 | 按已确认的停顿式方案实现 NodeMigration 状态机、运行状态检查点、校验流式传输、CRI 恢复、UID 绑定和故障保留 | migration/tests/01～04；检查计数和随机启动标识延续，记录暂停时间；不要求预拷贝或零停顿 |
| 2(4) 自定义节点动态管理 | label/taint/cordon 命令 | NodeProfile API、归属检查、状态回写、场景动作配置节点 | NodeProfile 修改、15、节点 label/taint 观察 |
| 3(1) 网络路径优化（本次简化） | K8s 已有 hostNetwork/hostPort；普通 Pod 使用 Flannel | HostNetworkWorkload API、节点/TCP 端口预留、冲突拒绝、回收；MPI TCP/独立 SSH 端口；无需额外硬件 | 16；同节点对/镜像摘要/资源的普通 Pod TCP vs hostNetwork TCP；不声称物理网卡直通 |
| 3(1) 多层次集合通信 | Open MPI 已有 tuned/HAN 等算法；MPI Operator 负责起任务 | 节点内 shared communicator、节点间 leader communicator；自编广播/规约/全收集及顺序还原 | 08/09/10：native vs hierarchical；可追加 HAN 对照 |
| 3(1) 计算通信融合 | MPI 已有 Iallreduce；原 YAML 有串行/非阻塞示例 | 可复用分块计算流水、两请求窗口、正确缓冲区生命周期、相同算子和输入的比较 | 11：serial vs overlap，两种 CPU 限额分别配对 |
| 3(2) 异构硬件与高精度 | 厂商驱动、device plugin API、MPI 数据类型机制 | 可配置 /dev 设备插件；统一资源声明；long double 传输与精度/误差记录 | 21-device、MPI mantissa_bits/relative_error；真实驱动必须先可用 |
| 4(1) 场景动态感知 | metrics-server；可另装 Prometheus/HPA/Koordinator | 节点采样 exporter、PromQL 策略、双阈值/连续样本/冷却、扩缩/迁移/节点配置动作 | 15 验证闭环；负载试验比较动作数、恢复时间、时延 |
| 4(2) 大规模/吞吐/实时验证 | 原有分散 YAML；KWOK/Kubemark 可测控制面 | 独立测试、真实 Pod 规模测试、iperf3、TCP 截止期测量、同条件配对报告 | 16/17/18/report.py；门槛由正式验收方案给出 |

“跨边界”当前落实为跨节点的资源分配、任务部署、扩缩和状态迁移。没有实现把远端 RAM 当成本机物理内存，或任意设备状态在线迁移；如果验收把这些也定义为必须能力，需要相应硬件、协议和设备状态后端，当前代码不能据此认定满足。

本次按已确认的最简方案，以 hostNetwork + TCP 替换旧 NIC/VF 直通模块，旧源码和入口已删除。它提供软件路径简化和对照测试，不能按原指标字面认定“物理网络直通”已经满足。没有硬件 RDMA 卸载，Soft-RoCE 也不在本版中；性能提升需实测。

## 3. 哪些属于自己补充，哪些属于集成

另核对了现有 k8s-test-main 的 MPI 示例：部分 YAML 设置 plm_rsh_agent=":" 禁用 SSH，且出现嵌套 mpirun。它们即使有多个 Worker，也不能仅凭 Launcher 日志认定通信真正跨节点。新任务显式使用 Operator 的 /etc/mpi/hostfile、正常 SSH 和实际 Worker placement 检查，避免把本地多进程当成分布式通信。

### 3.1 本项目新增逻辑

- 资源/节点/场景/主机网络 CRD 控制器及状态反馈。
- 设备静态清单到 kubelet Device Plugin API 的健康和分配适配。
- 跨节点状态迁移编排、CRI 代理授权和归档完整性校验。
- 分层广播、规约、全收集算法；不等大小分组的 rank 顺序还原。
- 分块计算与通信重叠，非阻塞缓冲区保护。
- 指标去重、过期拒绝、连续观测、冷却、伸缩边界及冲突检查。
- 配对比较、数据完整性、精度和截止期报告工具。

### 3.2 复用和集成

- 调度亲和性、反亲和性、抢占、污点容忍与原生 HPA：K8s。
- 容器隔离和恢复机制：containerd、runc、CRIU。
- hostNetwork、hostPort、调度互斥和 DNS：K8s；TCP/IP：Linux。
- MPI 传输、非阻塞请求和 datatype：Open MPI。
- 监控数据库/仪表盘/服务网格：Prometheus、Grafana、Istio。
- Helm chart 和上述项目的 RISC-V 构建入口属于适配交付，不应申报为重新实现整个生态。

## 4. 怎样比较才可信

### 4.1 通信

基线 A：当前 Open MPI 默认 collective；基线 B：同版本 Open MPI 的 HAN（若该构建包含）。实验组：本项目 hierarchical。MPI 本来就有层次算法，不能只与一个刻意低效的算法比较。

固定：物理机器/网卡/链路、MPI 版本、镜像 digest、rank 数、每节点 rank、消息长度、精度、CPU 绑定和限额。各次保留 placement；先预热，再重复；跨节点测试至少两个执行节点，每节点多个 rank。

融合按同输入、同计算次数、同精度比较 serial/overlap。资源限制对照需再比较 serial-limited/overlap-limited；不要把限额不同的两个结果直接算加速比。可用 MPI_ARGS_JSON 记录 MPI 调优参数。小消息/软浮点高精度/无异步进展时，本实现可能更慢，报告保留这种结果。

网络固定节点对、实际镜像摘要、资源/端口/流数/时长，对照普通 Pod TCP 和 hostNetwork TCP，轮换先后顺序，记录真实路由。两组因网络命名空间不同使用不同 Pod，不要求 Pod UID 相同，也不能混配不同轮次。现有链路若为 1Gb/s，软件方案不能突破物理上限。

### 4.2 场景

现有配置（或原生 HPA/Koordinator）与本策略分别运行同一负载轨迹。不要同时让多个组件写同一个 Deployment 的 replicas。记录负载、资源量、动作时刻、业务 P99、截止期违约、扩缩次数与恢复时间。vector() 仅用于功能注入，不能作为真实动态感知性能结果。

### 4.3 迁移

重建基线：删除并由镜像新建，预期内存状态丢失。迁移实验：启动标识和计数状态保持。记录停顿时间/总时间/归档大小，核对失败处理。停顿式有状态迁移已被确认为最终方案，预拷贝和零停顿不列为必做项，不继续优化停顿时间。现有 TCP 续接、任意卷/加速卡状态迁移仍在当前支持范围之外，详见 migration/README.md。

### 4.4 规模和实时

真实节点和虚拟节点结果分开。规模工具实测 Ready 数量/时间；不能凭两台机器报告超大规模吞吐。TCP 工具记录所有时延、P99、最大值和违约数；普通 K8s/Linux 的一次“零违约”结果不构成硬实时证明。若必须强实时保证，需要明确 deadline、允许违约率、调度优先级、CPU/IRQ 隔离、内核和负载约束，再在目标平台验证。

## 5. 参考上游能力

- [K8s 自动伸缩](https://kubernetes.io/docs/concepts/workloads/autoscaling/)
- [Open MPI 已有集合通信组件，包括 HAN](https://docs.open-mpi.org/en/main/tuning-apps/collectives/components.html)
- [MPI Iallreduce](https://docs.open-mpi.org/en/v5.0.7/man-openmpi/man3/MPI_Iallreduce.3.html)
- [K8s 主机网络 Pod 的 DNS 策略](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/)
- [Open MPI TCP 配置](https://docs.open-mpi.org/en/v5.0.7/tuning-apps/networking/tcp.html)
- [Koordinator 负载感知调度](https://koordinator.sh/docs/user-manuals/load-aware-scheduling)
- [KWOK 性能场景](https://kwok.sigs.k8s.io/docs/examples/performance/)
- [Istio 版本兼容表](https://istio.io/latest/docs/releases/supported-releases/)

