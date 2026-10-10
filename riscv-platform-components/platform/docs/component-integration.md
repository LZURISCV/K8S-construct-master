# 组件集成总览

当前代码在原 K8S-construct-master 上增加资源、设备、软件主机网络、通信/融合、场景、迁移、生态适配和验证模块，安装入口不再保留其他组件“尚未实现”的占位分支。

## 文档

- [逐指标现有能力与新增实现](comparison.md)
- [完整安装和使用](installation-and-usage.md)
- [源码逐模块讲解](source-code-guide.md)
- [迁移详细操作](../migration/README.md)
- [本地验证与实机待测项](../VERIFICATION.md)

## 角色

| 角色 | 内容 |
|---|---|
| 控制机 | K8s 控制面、control、migration 控制器、Helm、生态/测试入口 |
| 每个 Worker | kubelet/containerd/runc、exporter；按需要装 CRI 代理、CNI、设备插件 |
| 构建机 | 自有组件 Go 1.21/MPI；第三方源码所需工具链、RISC-V 镜像、匹配版 Istio Envoy/生态构建 |

组件通过 K8s API 解耦，节点管理和迁移使用 SSH；软件网络模块仅使用 K8s API。增加 Worker 无需改控制器源码，更新节点身份、模块、SSH 表和监控 targets。

## 接口和执行

| 接口 | 控制代码 | 执行 |
|---|---|---|
| ResourceClaim / ResourcePolicy | resource.py / scenario.py | Deployment、scheduler、kubelet |
| NodeProfile | resource.py | Node labels/taints/schedulable |
| Device Plugin API | device-plugin/main.go | 真正 /dev 映射 |
| HostNetworkWorkload | network.py | hostNetwork Pod、scheduler hostPort、现有节点 TCP |
| ScenarioPolicy | scenario.py | 扩缩、节点配置、迁移请求 |
| NodeMigration | migration/controller.py | CRI 代理、containerd/runc/CRIU |
| MPIJob | MPI Operator + job.py | 分层通信/融合 C 库 |
| Helm chart | ecosystem/install.py | Prometheus/Grafana/Istio |
| 测试 JSON | validation | 同条件对比与报告 |

## 顺序

基础集群 → exporter/资源控制 → 软件主机网络（已有联网网口）；设备按需选装 → 通信镜像/MPI Operator → 迁移运行时 → 生态 → 配置 Prometheus URL → 场景 → 独立测试和报告。

all 入口顺序安装控制机部分，不代替节点驱动、内核、CNI和镜像准备。

服务器保留 Go 1.21；自有模块已锁定兼容依赖，GOTOOLCHAIN=local。迁移所需的 containerd 2.1.5 附带 RISC-V 预编译文件，无需在服务器源码编译其 daemon。第三方生态按版本选择兼容源码或使用其他构建机准备的产物。

## 边界

- 最简网络为 hostNetwork + TCP，无专用网卡/RDMA/Soft-RoCE 依赖，不能等同物理 NIC/VF 直通。
- 迁移按已确认的方案允许停顿，预拷贝和零停顿不列为必做项；已有 TCP/任意卷和设备状态迁移仍在当前支持范围之外。
- 跨节点资源分配、部署/滚动调整不等于远端 RAM 透明借用。
- 设备插件需要真实可用的驱动和设备。
- Istio 提供源码/镜像/Helm 接入，RISC-V Envoy 全依赖与运行仍需实机验证。
- 性能和强实时指标需正式阈值与真实规模负载；普通 Linux/K8s 不提供硬实时保证。
- 控制器单活动实例，没有跨控制主机 leader election。

这些边界在 comparison.md 逐项列出，不把现有组件名称或未运行测试写为验收成果。
