# k8snode1：containerd 1.6 → 2.0.13 → 2.1.5 离线接入

## 范围和已确认环境

本步骤适用于本次回传的 k8snode1：openEuler 24.03、riscv64、cgroup v1；containerd 1.6.22.15.oe2403 由 systemd 管理，KillMode=process、Delegate=yes。CRIU check 已回传 Looks good、退出码 0；包内 containerd 2.1.5 --version 已在目标机执行成功。

保留的设置：root=/var/lib/containerd，state=/run/containerd，socket=/run/containerd/containerd.sock，pause=hub.oepkgs.net/ruoqing/pause:3.9，CNI=/opt/cni/bin 和 /etc/cni/net.d，runtime=io.containerd.runc.v2，SystemdCgroup=true，snapshotter=overlayfs。旧 CRI 顶层 systemd_cgroup=false 不能代替 runc options 下的 SystemdCgroup；此处以实际 runc options 为准。

containerd 官方升级说明对顺序小版本升级提供兼容约定，并提供 1.6/1.7 到 2.0 的迁移机制。本次经过 2.0，再到 2.1.5，不直接用 2.1.5 打开旧数据目录。参见 [官方升级说明](https://github.com/containerd/containerd/blob/v2.1.5/RELEASES.md#upgrade-path)。

补充包 rv-containerd-upgrade-riscv64.zip 包含官方静态 2.0.13 tar.gz、官方 SHA256 文件、脚本和本手册；2.1.5 继续使用现有平台源码包里的三个预编译程序。服务器无需 Go 编译器，也不升级 Go 1.21。

原始 2.0.13 文件来源：

- [官方版本发布页](https://github.com/containerd/containerd/releases/tag/v2.0.13)
- [RISC-V 静态 tar.gz](https://github.com/containerd/containerd/releases/download/v2.0.13/containerd-static-2.0.13-linux-riscv64.tar.gz)
- [官方 SHA256](https://github.com/containerd/containerd/releases/download/v2.0.13/containerd-static-2.0.13-linux-riscv64.tar.gz.sha256sum)

原始归档 SHA256 为 7767d4b8ecd96f11613679980fcbacb96eebb4f47028b73bae64ebdc74b85a4b。已核对归档内四个 ELF 为 EM_RISCV，均没有 PT_INTERP/PT_DYNAMIC。目标机仍需实际执行；准备脚本执行版本命令失败时停止。

每个 Bash 框单独执行，多行代码框整体复制。上一步报错时停止并保留输出。以下切换会重启 kubelet/containerd，单工作节点集群可能短暂不可用。MPI Operator 本轮已出现 API 续租超时并重启，根因尚未定位；不要将此既有问题记为本次升级成功或失败的证据。

## 1. Windows：上传补充包

把 ZIP 及同名 .sha256 文件放到桌面/2，在 Git Bash 执行：

```bash
cd ~/Desktop/2
```

```bash
git add -- rv-containerd-upgrade-riscv64.zip rv-containerd-upgrade-riscv64.zip.sha256
```

```bash
git commit --only -m "Add RISC-V containerd upgrade supplement" -- rv-containerd-upgrade-riscv64.zip rv-containerd-upgrade-riscv64.zip.sha256
```

```bash
git push origin main
```

## 2. 控制端 k8smaster：下载和传输

复用已经用于下载 CRIU 的浅克隆目录：

```bash
git -C "$HOME/rv-criu-download" pull --ff-only origin main
```

```bash
git -C "$HOME/rv-criu-download" sparse-checkout add '/rv-containerd-upgrade-riscv64.zip' '/rv-containerd-upgrade-riscv64.zip.sha256'
```

```bash
scp "$HOME/rv-criu-download/rv-containerd-upgrade-riscv64.zip" "$HOME/rv-criu-download/rv-containerd-upgrade-riscv64.zip.sha256" lzu@192.168.102.125:/home/lzu/
```

## 3. 工作端 k8snode1：准备，不重启

```bash
cd "$HOME"
```

```bash
sha256sum -c rv-containerd-upgrade-riscv64.zip.sha256
```

```bash
unzip rv-containerd-upgrade-riscv64.zip
```

```bash
cd "$HOME/rv-containerd-upgrade"
```

```bash
sha256sum -c SHA256SUMS
```

```bash
sudo bash "$HOME/rv-containerd-upgrade/prepare.sh" "$HOME/riscv-platform-components"
```

准备脚本要求 Python 3.11+ 的标准库 tomllib。它检查实际 daemon/版本/服务属性、文件校验值和本次确认的配置，将二进制分别放到 /opt/rv-platform/containerd/2.0.13/bin、2.1.5/bin。分别使用两个版本的 config migrate 从原始 v2 配置生成配置，避免把 CNI 的 bin_dir 迁移遗漏在已生成的 v3 配置中。

check_config.py 比较 root/state/socket、pause、CNI、runtime、runc/cgroup 和 snapshotter，同时私下比较 registry 配置；终端不打印 registry 凭据。任何不一致停止。生成的配置权限 0600，原 /etc/containerd/config.toml 保留。

输出应有 configurationChecked=true 和 PREPARED。准备脚本保留配置、systemd 单元文本、镜像清单和任务清单，位置为 /var/lib/rv-containerd-upgrade/backup.*。这是配置与清单备份，不是完整运行时数据快照，不提供跨版本自动降级。

准备阶段不重启服务，也不接入迁移代理。首次实机执行应先回传准备输出；如果停止，后续两个 switch 命令均不要执行。重复准备会拒绝覆盖已有 prepared.sha256，避免改写已准备或正在使用的文件。

### 旧包的 registry 校验误报

旧版 check_config.py 直接比较 registry 字典。迁移序列化可能省略空表或补入空字段，导致语义相同的配置被判为不同。修订版仅将 registry 顶层的空 config_path 和空 mirrors/configs/auths/headers 表视为缺省值；保留并逐项比较所有非空设置、未知字段和嵌套条目，不忽略镜像地址、凭据、TLS 设置或证书路径的真实变化。

如果旧包停止于 Registry settings changed，且 prepared.sha256 不存在，运行时仍未切换。修复旧包校验器并更新其 SHA256SUMS 条目，或者重新解压修订后的补充包，再重跑 prepare.sh；无需删除原始配置或备份。修复后若仍停止，说明还有不等价的配置差异，需要继续定位，不能删除检查或手工伪造 prepared.sha256。

config migrate 的 Ignoring unknown key 警告表示新版本不识别某些旧字段，不能仅凭警告就断定升级成功或失败。本次日志里流程继续运行到了 registry 检查；必须以准备检查通过及后续实际服务/CRI 检查结果为准。

## 4. 控制端：暂停向节点安排新 Pod

确认第 3 节准备完成、没有正在执行的业务任务后再进行本节及后续切换。本轮已回传的工作负载包括 Flannel、CoreDNS、metrics-server、MPI Operator、Prometheus 和 hostNetwork TCP 服务；升级期间保留其 Pod 对象。

```bash
kubectl --kubeconfig "$HOME/.kube/rv-admin.kubeconfig" cordon k8snode1
```

## 5. 工作端：切换到 2.0.13

```bash
sudo bash "$HOME/rv-containerd-upgrade/switch.sh" 2.0.13
```

脚本核对准备时记录的文件 SHA256、当前服务进程和 Server 版本，拒绝跳过升级阶段。它停止 kubelet、写入 systemd drop-in、重启 containerd，等待实际 Server 版本以及 images/runtime/CRI 三个插件状态为 ok，再启动 kubelet。命令输出 SWITCHED 只说明这些本机条件通过。

如果重启或插件检查失败，脚本停止并保留现场，节点保持 cordon；不要继续 2.1.5，不要盲目用 1.6 重新打开已升级的数据。查看：

```bash
sudo journalctl -u containerd.service -u kubelet.service --no-pager -n 100
```

## 6. 控制端：核对过渡阶段

```bash
kubectl --kubeconfig "$HOME/.kube/rv-admin.kubeconfig" wait --for=condition=Ready node/k8snode1 --timeout=180s
```

```bash
kubectl --kubeconfig "$HOME/.kube/rv-admin.kubeconfig" get nodes -o wide
```

```bash
kubectl --kubeconfig "$HOME/.kube/rv-admin.kubeconfig" get pods -A -o wide
```

节点应为 Ready,SchedulingDisabled，CONTAINER-RUNTIME 应更新为 containerd://2.0.13。等待条件可能仍读到切换前的 Ready，因此必须同时核对运行时版本已经更新。系统 Pod 应恢复正常；有异常时先提供输出，再继续。

## 7. 工作端：切换到 2.1.5

```bash
sudo bash "$HOME/rv-containerd-upgrade/switch.sh" 2.1.5
```

此阶段要求实际服务来自本次准备的 2.0.13。成功后按第 6 节在控制端再次检查，CONTAINER-RUNTIME 应更新为 containerd://2.1.5。

## 8. 工作端：安装迁移代理

在 2.1.5 的节点/系统 Pod 接入检查通过后执行：

```bash
sudo bash "$HOME/riscv-platform-components/platform/install.sh" migration-node
```

```bash
sudo systemctl status rv-migration-proxy.service kubelet.service --no-pager -l
```

```bash
sudo /usr/local/libexec/rv-migrate-runtime preflight
```

原安装器备份 kubelet 环境配置，把 runtime/image 端点接入 /run/rv-migration/cri.sock，并验证 CRI RPC 后重启 kubelet。preflight 是节点接入检查，不执行容器迁移；仍不等于已验证跨节点恢复。

## 9. 控制端：恢复调度

先按第 6 节确认节点 Ready、运行时 2.1.5、系统 Pod 状态，再执行：

```bash
kubectl --kubeconfig "$HOME/.kube/rv-admin.kubeconfig" uncordon k8snode1
```

此时仅完成工作端运行时和迁移代理接入。控制端 rv-migration-controller 以及到节点的受信任 SSH 通道仍需安装和配置。集群目前只有一个可调度工作节点，不能据此宣称已经完成跨节点迁移。

## 本地核对与实机边界

本地核对原始官方归档 SHA256、ELF 架构/静态链接、脚本 Bash 语法、配置比较器单元测试和交付 ZIP 校验。没有在 Windows 上运行 RISC-V daemon、systemd 切换或 CRI 重接测试。步骤 3、5、6、7、8 的实机结果分别记录，不将准备、切换、插件接入和功能验收合并为同一结论。
