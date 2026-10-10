# RISC-V K8s 构建脚本与平台组件

基于原 K8S-construct-master 的当前源码。目标：openEuler 22 系列、riscv64、cgroup v1、多执行节点。原始 ZIP 不代表本目录最新代码。

## 阅读入口

- **[完整包解压与异机使用入口](START-HERE.md)**
- **[现有方案与新增能力对照](platform/docs/comparison.md)**
- **[完整安装与使用手册](platform/docs/installation-and-usage.md)**
- **[详细源码讲解](platform/docs/source-code-guide.md)**
- [集成总览](platform/docs/component-integration.md)
- [迁移专门手册](platform/migration/README.md)
- [验证记录与实机待测项](platform/VERIFICATION.md)

## 源码组件

| 组件 | 入口 |
|---|---|
| 资源 HAL、资源池与节点配置 | platform/control/resource.py |
| 节点监控 | platform/node/agent.py |
| 真实设备接入 | platform/device-plugin/main.go |
| 最简 hostNetwork + TCP、端口管理 | platform/control/network.py |
| 分层广播/规约/全收集、计算通信重叠 | platform/communication/rv_collective.c |
| 场景感知与弹性 | platform/control/scenario.py |
| 跨节点状态迁移 | platform/migration |
| Helm/Istio/Prometheus/Grafana 适配 | platform/ecosystem |
| 独立测试、原始数据、对比报告 | platform/validation |

这是首版源码实现与集成入口，不代表真实 RISC-V 集群全部验收。迁移按已确认的方案采用停顿式有状态迁移，预拷贝和零停顿不列为必做项；支持的任务范围见迁移手册。Istio 需要匹配的可运行 RISC-V Envoy；吞吐、规模、截止期需实测。

网络已替换为 hostNetwork + TCP，复用现有联网网口。旧直通源码/脚本已删除；无需 Multus、SR-IOV、RDMA 或 Soft-RoCE。软件路径简化与物理网卡直通有区别，详见对比文档。通信镜像改用新 tag 0.2.0，按最新手册构建。

自有 Go 组件已兼容服务器现有 **Go 1.21**，构建脚本禁止自动升级工具链。完整包附带迁移所需的 RISC-V containerd 2.1.5 程序；服务器无需安装 Go 1.27。详见安装手册第 1.2 节。

## 安装入口

~~~bash
bash platform/install.sh
~~~

已有集群不重跑初始化，按手册安装附加组件。新集群使用 k8s_master.sh、k8s_node.sh、k8s_master_other_components.sh。k8s_master-1.sh 为原另一入口，未对接本方案，不混跑。

原脚本已修正：逐节点凭据、不复制控制 CA 私钥、Worker 不独建 etcd、节点名/IP一致、systemd cgroup、Service CIDR/DNS、网络转发和端口、标准 scheduler 描述、新版 kubelet cgroup v1 配置和匿名访问。软件源版本及部分原网络/Operator 镜像仍需现场固定/审核。

## 测试

01～12 对应大纲 3-2-0001～0012；13～22 补充 Pod 反亲和、资源池、感知、网络、时延、规模、生态、隔离、设备、周期计算截止期。每项独立入口，迁移测试在 migration/tests。

~~~bash
bash platform/validation/00-local-unit.sh
~~~

性能比较保留同镜像、节点、资源、消息/计算参数。K8s/CNI/Open MPI/HAN/CRIU 等原能力归上游，不算作重新研发。开源贡献统计不在本次范围。
