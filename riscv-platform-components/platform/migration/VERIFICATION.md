# 本次本地验证记录

日期：2026-10-10。

## 1. 环境

- 验证主机：Windows/amd64。
- 自有工具 Go：官方发行版 go1.21.0，下载包经过官方 SHA256 校验；GOTOOLCHAIN=local，使用现有编译器。
- 第三方 containerd 2.1.5：由交付构建机的 Go 1.27.2 交叉编译，运行程序已附带；服务器不需要安装该编译器。
- Python：本地工具运行时 Python 3.12；组件使用 Python >= 3.9 可用的标准库接口。
- Bash：Git for Windows 的 Bash，仅用于语法检查。
- YAML 解析：临时目录中的 PyYAML 6.0.2，使用 safe_load_all。

## 2. 已通过

### Go：6 个测试

- 归档与源命名空间/Pod UID/容器身份一致。
- 恢复授权绑定精确目标 Pod UID；归档变更时拒绝恢复。
- 普通 CreateContainer 不被改写。
- 路径穿越被拒绝。
- 通过真实 Unix socket/gRPC 交换验证代理转发 Version RPC；上游是模拟运行时。
- 传输截断时不发布、不覆盖已有有效归档。

实际 Go 1.21.0 下，`go test -mod=readonly -v ./...` 和 `go vet -mod=readonly ./...` 通过，Linux/riscv64 交叉编译通过。两个 Go 模块的依赖下载和校验通过；完整 migration-build 入口用 Go 1.21.0 实际执行成功。SDK 改为 Go 1.21 兼容的 containerd 1.7.27，通过稳定 gRPC API 连接实际 2.1.5 daemon；没有降低 daemon 的恢复能力要求。

### Python：10 个测试

- 拒绝控制器管理的源 Pod。
- 拒绝外部卷和不符合要求的重启策略。
- 模拟完整迁移状态机、精确授权和成功后解除目标 ownerReference。
- 传输失败保留源记录、锁和 finalizer。
- 源 Pod 名称被复用时拒绝删除新 UID。
- 请求 spec 被修改时拒绝进入检查点。
- 资源单位解析。
- 控制器重启后继续清理成功事务的 finalizer。
- 目标资源不足时不执行检查点。
- kubelet 端点改写保留引号、其他配置及幂等性。

这些测试使用模拟 K8s 和节点接口，不代表已经执行真实容器迁移。

### 脚本和配置

- 3 个修改后的原构建脚本和 12 个新增 Bash 脚本经过 `bash -n` 检查。
- 新增 Python 文件经过 AST 语法检查。
- CRD/RBAC YAML 和示例 JSON 经过解析检查。
- Markdown 中的 Bash 命令块经过语法检查，内部文件链接已核对。

语法检查不等于真实 systemd、软件源、证书、网络和 K8s API 安装回归。

## 3. 编译产物

文件：`bin/rv-migrate-runtime`。

- 目标：Linux/riscv64，CGO_ENABLED=0。
- ELF64 little endian，e_machine=243（RISC-V）。
- 编译器：go1.21.0。
- 文件大小：15,794,176 字节。
- SHA256：`dbb334f39e81abf73bfed2aa6d727956c54aa3ffd389e478d693ac080e5aad26`。
- 校验文件：`bin/SHA256SUMS`，相对文件名，可在 bin 目录执行 sha256sum -c。

不同 Go 版本或构建环境重新编译后，哈希和文件大小可能变化。build.sh 会重新生成校验文件。

另附 containerd-bin/containerd、containerd-shim-runc-v2、ctr，三者由固定的 containerd v2.1.5 源码交叉编译，均为 Linux/riscv64 静态 Go ELF。来源、构建信息、依赖、许可证和独立 SHA256 清单随包提供。默认 build-containerd.sh 的实际校验已通过；损坏文件校验失败的路径已检查。选择源码重建时 Go 1.21 会被明确拒绝，上游要求更高编译器，服务器可继续使用附带运行程序。

## 4. 实机待验证与支持范围

按已确认的需求，停顿式有状态迁移作为最终方案；预拷贝和零中断不列为必做项，也不列为待完成优化。实际暂停时间仍需记录。

### 需要目标服务器验证

- openEuler/RISC-V 软件源、CRIU/runc 和目标内核兼容性。
- containerd 2.1.5 daemon 的目标机运行和恢复能力；本地已交叉编译并交付 daemon、shim、ctr 及迁移工具，尚未在用户集群执行。
- 修改后的构建脚本、迁移安装脚本和 systemd 服务实际运行。
- 真实 K8s API、CRI 检查点/恢复、任务内存连续性和生命周期。
- 实际暂停时间、迁移耗时、业务正确性。

### 当前支持范围以外的能力

- 已有 TCP 连接续接。
- Deployment/StatefulSet、副本协调、外部卷、设备状态迁移。
- 自动回滚、多控制机 leader election。
- 其他组件已在后续本次交付加入，详见 ../VERIFICATION.md 和 ../docs/comparison.md；本文件只记录迁移模块。
- 全项目超大规模、高吞吐、强实时验收及测试报告。

因此当前成果应描述为“节点迁移组件首版源码、构建产物和本地验证完成”，不能描述为“全部指标已经满足”或“已在 RISC-V 集群通过在线迁移验收”。
