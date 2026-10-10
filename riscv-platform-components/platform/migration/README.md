# 节点迁移组件安装和使用手册

## 1. 本次实现了什么

本组件将一个正在运行的容器任务从节点 A 迁移到节点 B，保留进程和内存状态。目标端通过 K8s/CRI 恢复，继续由 kubelet 管理。

**按已确认的需求，停顿式有状态迁移作为最终方案：保存状态时停止源任务，随后传输和恢复，暂停时间包含传输与恢复。预拷贝和零停顿不列为必做项，不再继续优化停顿时间。** 本次完成代码、配置、构建产物和本地验证，尚未在你的 RISC-V 服务器实测。

迁移验收重点是支持范围内的任务跨节点恢复、进程内存状态延续、恢复后的容器管理和失败处理。记录实际暂停时间用于说明运行结果，不设置零停顿目标。

现在可以先完成代码和安装准备，不需要立即启动迁移或演示。

### 支持范围

- Linux/RISC-V，当前配置默认只允许 riscv64。
- containerd 2.1+ 的 2.x 系列，OCI runtime 为 `io.containerd.runc.v2`。实现按 containerd 2.1.5 官方源码接口核对。
- 源/目标节点的架构、内核版本、cgroup 模式、containerd/runc/CRIU 版本一致。
- cgroup v1 或 v2，源目标一致；你的环境应保持 v1。
- 一个独立 Pod、一个容器、`restartPolicy: Never`。
- 不自动挂载 ServiceAccount token，不使用外部卷、设备、扩展资源、init/sidecar/ephemeral container。
- 不使用探针、生命周期钩子、交互式终端和 host/shared namespaces。
- 不使用 affinity、自定义 RuntimeClass、scheduling gates。
- 没有已建立的 TCP 连接；首版不保持源 Pod IP 和已建立连接。
- 基础镜像有可用的 name@digest 引用，目标端能通过匿名 HTTPS 仓库获取它。不支持需要 imagePullSecrets 的恢复路径。

源端会检查镜像隐式挂载和已建立 TCP 连接。超出范围时拒绝执行；发生源端检查点错误时保留信息供检查，不自行判定“肯定没停”。

这些限制界定当前支持的计算任务。允许迁移停顿的约定不扩大卷、连接或设备状态的支持范围。

## 2. 文件和职责

| 文件 | 作用 |
|---|---|
| `controller.py` | NodeMigration 状态机、工作负载验证、节点选择检查、传输协调、恢复和提交 |
| `runtime/main.go` | 原生检查点、归档、完整性验证、授权、CRI 代理、运行时恢复验证 |
| `manifests/crd.yaml` | 迁移 API |
| `manifests/rbac.yaml` | 控制器专用权限和凭据 |
| `scripts/build.sh` | 构建 Linux/RISC-V 工具 |
| `scripts/build-containerd.sh` | 默认校验包内 RISC-V containerd 2.1.5；不自动替换服务；另有构建机源码重建入口 |
| `scripts/install-node.sh` | 节点安装与 kubelet 端点接入 |
| `scripts/install-controller.sh` | 控制机安装 |
| `scripts/disable-node.sh` | 切回安装前的 kubelet CRI 端点 |
| `retry.py` | 管理员显式重试可安全重复的阶段 |
| `tests/01`～`05` | 分开的预检、任务创建、迁移、内存验证和本地单元测试 |

## 3. 先检查现有机器

以下命令每个代码块是一条可以独立执行的命令。多行命令用反斜杠连接，整体是一条命令。

在每个工作节点执行：

```bash
cat /etc/os-release
```

```bash
uname -m
```

```bash
uname -r
```

```bash
kubelet --version
```

```bash
containerd --version
```

```bash
runc --version
```

```bash
findmnt -R /sys/fs/cgroup
```

在控制机执行：

```bash
kubectl get nodes -o wide
```

至少需要两个能够运行容器的节点才能实际跨节点迁移。控制器可以先在一台控制机安装并等待后续节点。控制机不必兼任工作节点。

**K8s 版本另行核对：**现有脚本从软件源安装，没有锁定版本。containerd 2.x 需要兼容 CRI v1 的 kubelet。Go 1.21 是本项目构建工具版本，与运行中的 kubelet/containerd 版本分别核对。较新 K8s 对 cgroup v1 的默认策略也可能不同，不能直接把 K8s、containerd 和系统包全部升级到 latest 后假定兼容。

## 4. 准备 CRIU 和编译工具

在工作节点上检查软件源：

```bash
dnf info criu runc golang
```

软件源存在合适版本时安装：

```bash
dnf install -y criu runc python3 openssh-clients
```

```bash
criu --version
```

```bash
criu check
```

`criu check` 必须成功。它只能检查基础环境，不等于已经证明目标业务可迁移。若 openEuler 22 的 RISC-V 软件源没有可用 CRIU，需要先准备支持该内核/架构的 CRIU 构建；脚本不会用 x86 二进制代替。

如果只使用附带的 RISC-V 迁移工具，节点不需要 Go。自行构建已支持现有 Go 1.21，不用安装 1.27；构建脚本强制 GOTOOLCHAIN=local，禁止自动升级工具链：

```bash
go version
```

## 5. 构建迁移工具

假设把修改后的整个 `K8S-construct-master` 内层目录放到 `/opt/K8S-construct-master`。后续所有相对路径以此为起点。

```bash
cd /opt/K8S-construct-master
```

在已有 Go 环境的构建机执行：

```bash
bash platform/install.sh migration-build
```

脚本默认交叉编译为 Linux/riscv64，输出 `platform/migration/bin/rv-migrate-runtime`，Go 1.21 兼容依赖由 go.mod/go.sum 锁定。客户端使用 containerd SDK 1.7.27 的稳定 gRPC API，实际 daemon 仍为 2.1.5，并未将运行时降为 1.7。checkpointctl 1.1.0 提供相同的 config.dump 元数据结构，新增常量 statusDumpFile 保存 containerd 2.1 恢复需要的 status.dump；检查点、归档和恢复授权流程保留。可在 x86 Linux 构建机编译后复制到 RISC-V 节点。

附带的二进制已在本地交叉编译，可以先核验：

```bash
cd /opt/K8S-construct-master/platform/migration/bin
```

```bash
sha256sum -c SHA256SUMS
```

如果从 Windows 复制后没有执行权限：

```bash
chmod 0755 rv-migrate-runtime
```

```bash
cd /opt/K8S-construct-master
```

## 6. 现有 containerd 不支持恢复时怎么办

如果当前是 containerd 1.x 或 2.0，迁移预检会停止。先准备升级过的空载节点，不要在未知运行负载的情况下直接覆盖运行时。

完整包附带预编译的 Linux/riscv64 containerd 2.1.5、shim 和 ctr。默认入口只检查这些文件，不调用 Go 编译，不改变运行中的服务：

```bash
bash platform/migration/scripts/build-containerd.sh
```

文件位于 `platform/migration/containerd-bin/`，包括 containerd、containerd-shim-runc-v2、ctr、SOURCE_COMMIT、BUILD-INFO.json、SHA256SUMS 和 licenses/。校验应全部为 OK。

这些程序在交付构建机预编译，构建机使用的 Go 版本不要求安装到服务器。containerd 2.1.5 上游源码要求 Go 1.23+，因此服务器 Go 1.21 不执行它的源码构建。需要自行重建时，在另一台满足上游要求的构建机设置 CONTAINERD_BUILD_FROM_SOURCE=1 后运行同一脚本，再拷回 RISC-V 产物。默认步骤不要求这一操作。

以下切换步骤在确认空载、具备恢复条件的节点由管理员执行。先保存原配置：

```bash
cp -p /etc/containerd/config.toml /etc/containerd/config.toml.before-rv-upgrade
```

```bash
install -d -m 0755 /opt/rv-platform/containerd/bin
```

```bash
install -m 0755 platform/migration/containerd-bin/containerd platform/migration/containerd-bin/containerd-shim-runc-v2 platform/migration/containerd-bin/ctr /opt/rv-platform/containerd/bin/
```

用新版本迁移原配置，保留原有 root/state、镜像仓库、sandbox image 等设置：

```bash
/opt/rv-platform/containerd/bin/containerd --config /etc/containerd/config.toml config migrate > /etc/containerd/config-rv-migration.toml
```

检查生成配置；确保 runc 的 `SystemdCgroup = true`，pause 镜像为你的 RISC-V 可用镜像，CRI 插件未禁用，CNI 目录与原节点一致：

```bash
cat /etc/containerd/config-rv-migration.toml
```

```bash
mkdir -p /etc/systemd/system/containerd.service.d
```

创建覆盖文件，整个代码块是一条 heredoc 命令：

```bash
cat > /etc/systemd/system/containerd.service.d/30-rv-version.conf <<'EOF'
[Service]
Environment="PATH=/opt/rv-platform/containerd/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=
ExecStart=/opt/rv-platform/containerd/bin/containerd --config /etc/containerd/config-rv-migration.toml
EOF
```

```bash
systemctl stop kubelet
```

```bash
systemctl daemon-reload
```

```bash
systemctl restart containerd
```

```bash
systemctl start kubelet
```

```bash
/opt/rv-platform/containerd/bin/ctr version
```

`ctr version` 中 Server 才是实际运行的服务版本。原 `/usr/bin/containerd --version` 可能仍显示旧二进制的版本。迁移预检读取的是实际服务版本。

升级回退不由迁移组件的 disable-node.sh 负责；运行时数据格式跨版本回退需要单独验证，不能只恢复配置就假定可以降级。尚未执行这些升级步骤时，不会改变当前运行环境。

## 7. 在每个迁移节点安装

在节点执行：

```bash
cd /opt/K8S-construct-master
```

```bash
bash platform/install.sh migration-node
```

安装器依次执行环境预检、复制工具、创建 systemd 服务、备份 kubelet.conf、更新 CRI 端点、验证代理 RPC、重启 kubelet。代理接入后，迁移预检还会检查目标资源请求容量，并在停止源任务前预先按 digest 获取目标基础镜像。

```bash
systemctl status rv-migration-proxy --no-pager
```

```bash
/usr/local/libexec/rv-migrate-runtime preflight
```

```bash
journalctl -u rv-migration-proxy -n 50 --no-pager
```

新端点是 `unix:///run/rv-migration/cri.sock`，上游仍为 `/run/containerd/containerd.sock`。普通运行时及镜像服务调用转发给 containerd；只有授权恢复的 CreateContainer 被改写。

## 8. 在控制机准备 SSH 和配置

节点清单按实际 K8s 节点名填写，可增加任意数量节点。

```bash
install -d -m 0700 /etc/rv-platform
```

只在密钥不存在时生成：

```bash
test -f /etc/rv-platform/migration_ed25519 || ssh-keygen -t ed25519 -N '' -f /etc/rv-platform/migration_ed25519
```

为每个节点建立访问并核验主机指纹。以第一台节点为例：

```bash
ssh-copy-id -i /etc/rv-platform/migration_ed25519.pub root@192.168.50.71
```

```bash
ssh-copy-id -i /etc/rv-platform/migration_ed25519.pub root@192.168.50.72
```

配置和运行控制器的用户均为控制机 root，使用其 known_hosts。首版节点执行工具需要 root；SSH 密钥只用于受信任的集群管理通道。

```bash
cp platform/migration/config.example.json /etc/rv-platform/migration.json
```

```bash
vi /etc/rv-platform/migration.json
```

```bash
chmod 0600 /etc/rv-platform/migration.json /etc/rv-platform/migration_ed25519
```

修改 nodes 中的节点名、host、user、identityFile。节点名是 `kubectl get nodes` 返回的名字，不是随意取的别名。

安装控制器：

```bash
bash platform/install.sh migration-controller /etc/rv-platform/migration.json
```

```bash
systemctl status rv-migration-controller --no-pager
```

```bash
kubectl get crd nodemigrations.migration.riscv.io
```

```bash
journalctl -u rv-migration-controller -n 50 --no-pager
```

控制器使用专用 ServiceAccount 凭据，不将管理员 kubeconfig 复制到工作节点。创建 NodeMigration 的权限仍由集群管理员控制；不要向普通用户无差别开放跨命名空间迁移权限。

控制器初版运行一个实例，通过主机文件锁防止同机重复启动，未实现多控制机 leader election。

## 9. 不演示时到哪里可以停

完成上述构建、配置和安装后即可停在这里。没有 NodeMigration 请求时，控制器不会迁移业务。

若现在只准备代码，则构建和静态检查结束后即可停；不必安装节点代理，也不必切换 containerd。

## 10. 将来运行分开的测试

### 10.1 预检

```bash
bash platform/migration/tests/01-preflight.sh /etc/rv-platform/migration.json
```

### 10.2 构建验证镜像

在具备 RISC-V 镜像构建能力的环境中，以你已经验证的 openEuler/RISC-V 基础镜像构建。它需要支持 dnf，目标仓库需能由恢复路径匿名通过 HTTPS 获取。

```bash
docker build --build-arg BASE_IMAGE=你的openEuler-RISC-V基础镜像 -t 你的仓库地址/rv-memory-counter:v1 platform/migration/examples
```

```bash
docker push 你的仓库地址/rv-memory-counter:v1
```

没有填写实际镜像地址前，这两条命令是模板，不能原样执行。启动标识和计数只保存在进程内存中，没有写入持久卷。

### 10.3 创建源容器

```bash
export SOURCE_NODE=k8snode1
```

```bash
export COUNTER_IMAGE=你的仓库地址/rv-memory-counter:v1
```

```bash
bash platform/migration/tests/02-create-counter.sh
```

### 10.4 发起迁移

```bash
export TARGET_NODE=k8snode2
```

```bash
bash platform/migration/tests/03-migrate.sh
```

```bash
kubectl get nmg -n migration-test
```

### 10.5 验证内存状态

```bash
bash platform/migration/tests/04-verify-memory.sh
```

要求启动标识一致、计数从原进度继续。运行时还会验证目标容器的 restored=true 标记。日志采样间隔仅作观察，不能代替精确暂停时间测试；跨机器时间比较前应保证时钟同步。

### 10.6 单独运行本地单元测试

```bash
bash platform/migration/tests/05-local-unit.sh
```

这些是模拟控制面/运行时接口的代码测试，不启动实际业务容器。

## 11. 正常使用 API

源 Pod 必须先满足第 1 节限制，并包含下述配置。以下为 YAML 内容，不是 Bash 命令：

```yaml
metadata:
  annotations:
    migration.riscv.io/enabled: "true"
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
```

创建请求，整个代码块是一条命令：

```bash
kubectl create -f - <<'EOF'
apiVersion: migration.riscv.io/v1alpha1
kind: NodeMigration
metadata:
  name: my-migration
  namespace: migration-test
spec:
  sourcePod: memory-counter
  targetNode: k8snode2
EOF
```

源 Pod 与 NodeMigration 必须在同一命名空间。

状态流转：Pending → Validating → Checkpointing → Transferring → Restoring → WaitingForRestore → Committing → Succeeded。

- Failed：检查点开始前失败；源任务仍未由本组件停止。
- RecoveryRequired：检查点阶段或之后出现问题，不能假定源任务仍运行。源状态、归档和 finalizer 保留。
- Succeeded：目标由运行时恢复并运行、源 Pod 清理完成。应用正确性仍由专门用例验证。

恢复出来的是新 Pod UID，不能要求源目标 UID 相同。目标 Pod 名保存在 status.targetPod。

## 12. 失败后如何处理

先读取状态：

```bash
kubectl get nmg my-migration -n migration-test -o yaml
```

```bash
journalctl -u rv-migration-controller -n 100 --no-pager
```

源/目标归档位于各自节点的 `/var/lib/rv-migration/<请求UID>/`，权限为目录 0700、文件 0600。检查点包含任务内存，按业务敏感数据保管。

源端原生检查点镜像在 containerd 的 k8s.io namespace 中名为 `rv-checkpoint-<请求UID>`。即使归档导出失败，也保留已创建的原生检查点内容供恢复诊断。

传输、恢复等待和提交阶段的临时错误，可以在确认目标状态后显式重试：

```bash
python3 platform/migration/retry.py my-migration -n migration-test
```

检查点阶段失败、目标已经退出或 spec 被修改时，工具拒绝自动重试，需先检查状态。首版没有自动回滚到源节点的功能，也不会在目标可能已经执行过任务后擅自重放旧状态。

失败对象保留 finalizer，避免用户删除请求时丢失恢复线索。确认业务和归档处置完成后，由管理员手动处理；不提供一条删除所有归档的清理脚本。

成功后会解除目标 Pod 对迁移请求的 ownerReference，删除成功请求不会连带删除迁移后的业务 Pod。归档不会自动删除。

## 13. 回退 CRI 代理

确认没有正在进行的迁移，在对应工作节点执行：

```bash
bash platform/migration/scripts/disable-node.sh
```

该操作恢复安装前 kubelet.conf、切回原运行时端点并停止代理，保留归档。它不降级 containerd，也不自动恢复已经停止的任务。

## 14. 当前验收边界

可以审核：源码、组件接口、构建结果、代码测试、安装脚本和独立测试用例。

仍需真实服务器验证：CRIU/runc 与目标内核兼容性、实际内存状态迁移、CRI 恢复的容器生命周期、脚本安装回归和暂停时间。预拷贝和零停顿不属于本次必做范围；现有本地验证尚不能据此证明任意业务、网络/设备状态迁移或全部项目指标已经通过。
