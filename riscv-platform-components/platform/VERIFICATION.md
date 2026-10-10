# 本次源码交付与验证记录

日期：2026-10-10。环境：Windows x86_64，本地没有连接用户的 openEuler/RISC-V 集群。本文件记录已实际执行的检查，不作为硬件验收报告。

## 1. 交付内容

原 K8s 脚本集成修正；资源/节点/场景/hostNetwork TCP 控制器；节点 exporter；通用设备插件；MPI 分层广播/规约/全收集和计算通信流水；迁移运行时及控制器；生态构建/镜像/Helm 入口；独立功能/性能测试；详细安装/源码/对比文档。

## 2. 实际通过的本地检查

| 检查 | 结果 | 证明范围 |
|---|---|---|
| 平台 Python 单元测试 | 45 项通过（本次重跑） | 资源/场景原有检查，hostPort 预留/回收/冲突，MPI TCP/SSH 设置和 Worker 身份，网络证据与配对 |
| 迁移 Python 单元测试 | 10 项通过 | 状态机、UID、容量、故障保留、kubelet 配置 |
| 迁移 Go 测试 | 实际 Go 1.21.0：6 项通过；go vet 通过 | 归档/校验/身份、真实本地 gRPC 转发、损坏传输保护 |
| 设备插件 Go 测试 | 实际 Go 1.21.0：1 项通过；go vet 通过 | 静态配置/重复和路径保护 |
| Linux/riscv64 交叉编译 | 实际 Go 1.21.0：迁移和设备插件成功 | 可以生成目标架构 ELF，不表示已在目标机运行 |
| MPI C 编译 | rv_collective.c、benchmark.c 用 Zig C 编译器 + Microsoft MPI 公共头生成目标文件，-Wall/-Wextra/-Werror | C 类型/API 和编译警告检查；不是 Linux Open MPI 链接或多节点运行 |
| Bash | 49 个 .sh 语法通过 | shell 语法，不是软件源/systemd/集群运行 |
| Python/JSON/YAML | AST、JSON、普通 YAML 解析通过 | 可解析性 |
| Helm | 官方 Helm 3.17.3（下载 SHA256 核验）lint --strict 通过；模板渲染及内部 Prometheus/Grafana YAML/JSON 解析通过 | Chart 结构和模板，不是容器镜像启动 |
| TCP 工具 | localhost 实际 echo，4KiB 和 4MiB payload 校验通过 | 分帧、数据校验、工具自身计时；不计作 RISC-V 网络性能 |
| 文档 | 280 个 Bash 命令块与 19 个内部文件链接核验通过 | 复制命令的结构检查 |
| 周期计算 C 工具 | 最终 realtime.c 用 Zig 交叉编译 Linux/riscv64 成功 | POSIX 代码/目标构建，不是实际实时性能 |
| Go 1.21 构建入口 | migration-build 完整执行成功；两个模块 go mod download / go mod verify 通过 | 自有源码和锁定依赖适配 1.21，不依赖自动工具链升级 |
| 编译器保护 | 实际 1.21.0 可用、模拟 1.20 被拒绝、GOTOOLCHAIN 强制 local | 服务器继续使用现有编译器 |
| containerd 预编译包 | 三个 RISC-V ELF 及默认脚本校验通过；损坏文件被拒绝 | 预编译产物可交付，不表示已在目标机运行 |

合计 62 项单元测试（55 Python + 7 Go）；本次修改全部重跑，Go 测试及交叉编译使用实际 Go 1.21.0，GOTOOLCHAIN=local。测试使用假 K8s/节点对象的地方明确为模拟；没有把这些用作真实容器功能验收。

工具版本：自有迁移/设备组件使用官方 Go 1.21.0 windows/amd64（下载包经官方 SHA256 核验）；第三方 containerd 2.1.5 预编译程序由交付构建机的 Go 1.27.2 生成，该编译器无需安装到服务器。Zig 0.14.1 C 编译器；Helm 3.17.3 仅用于本地 Chart 验证。实际部署生态版本由目标集群兼容性和固定源码决定。

## 3. 产物

迁移现有交叉编译产物：migration/bin/rv-migrate-runtime，由 Go 1.21.0 构建，15,794,176 字节，ELF e_machine=243（RISC-V），SHA256：

dbb334f39e81abf73bfed2aa6d727956c54aa3ffd389e478d693ac080e5aad26

设备插件本地交叉编译验证产物生成在系统临时目录；源码和 go.sum 已交付，安装器按源码生成。临时产物未放进交付源码。

迁移运行时所需的 containerd 2.1.5、containerd-shim-runc-v2 和 ctr 已交叉编译并随包提供；版本、上游提交、每个程序的编译信息和 SHA256 记录在 migration/containerd-bin/BUILD-INFO.json，许可证文本位于该目录的 licenses/。三者均为 Linux/riscv64，CGO_ENABLED=0。默认 build-containerd.sh 只校验附带程序，不调用 Go；显式选择上游源码重建且编译器为 1.21 时，会在构建前解释上游 1.23+ 要求并退出，不自动升级。

## 4. 必须在目标环境执行

- openEuler 软件源、Go/Open MPI/CRIU/runc、内核与 cgroup v1 兼容性。
- 真实 K8s CRD/RBAC/systemd 安装及所有独立用例。
- containerd 2.1.5 daemon、真实检查点/恢复、应用内存连续性和暂停时间。
- 设备真实驱动/设备映射、健康/回收和业务计算。
- hostNetwork TCP 的真实多节点端口调度/回收、路由、吞吐与延迟，以及普通 Pod TCP 配对对照；专用 NIC/VF 直通模块已删除，不作为当前交付能力。
- MPI 不等分组、非零 root、高精度、多节点数值正确性和 native/HAN 对比。
- 生态镜像构建和运行，特别是匹配版 Istio Envoy 的 RISC-V 工具链/第三方依赖与服务网格联调。
- 实际场景负载的闭环效果、超大规模、强实时截止期和正式吞吐门槛。

## 5. 未覆盖的通用能力

迁移按已确认的需求采用停顿式有状态方案。预拷贝和零停顿不列为必做项，不再作为待完成优化；迁移实机验证仍按第 4 节执行。

其他未覆盖能力：已有 TCP 续接、任意卷/设备状态迁移、Deployment/StatefulSet 状态迁移协调、跨控制机 leader election、远端 RAM 透明借用、任意驱动的自动移植和硬实时保证。

当前网络采用已确认的最简 hostNetwork + TCP，复用已有联网网口；不实现物理 NIC/VF 直通或硬件 RDMA。MPI 默认同样使用 TCP，节点内保留可用共享内存，需重建 0.2.0 镜像。旧网络模块、安装依赖和测试脚本已经从交付目录移除。

因此可描述为“当前简化方案列出的首版源码和集成/验证入口已补齐；本地检查完成”。不能描述为“全部指标已经在 RISC-V 数据中心验收通过”。生态源码构建失败或硬件能力不满足时，相关指标仍未完成，comparison.md 中保留相应边界。


## 6. 完整包与异机使用

交付 ZIP 含全部本项目源码、安装/构建/独立测试脚本、配置示例、Dockerfile、Helm chart 及全部 Markdown 文档。根目录新增 START-HERE.md，说明上传、解压、校验、放置标准源码目录及角色执行步骤；SHA256SUMS 覆盖包内除清单自身以外的全部文件。

本次核对压缩包内所需入口齐全、旧网络模块不存在、Shell 文件和 SHA256 清单使用 LF；全部 280 个 Bash 块语法和 19 个内部文档链接核验通过。打包后另逐文件核对 ZIP 与当前源码、内部 SHA256 清单及 ZIP CRC。此次完成自有 Go 1.21 兼容、构建入口、运行时预编译文件及文档更新，没有宣称目标集群测试已经通过。其余系统依赖、第三方源码/Go 模块及容器镜像按手册另行准备，当前不是包含全部依赖的离线部署包。
