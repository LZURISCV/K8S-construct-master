# openEuler 24.03 / RISC-V 的 CRIU 离线补充包

适用的已回传环境：openEuler 24.03 (LTS)、riscv64、glibc 2.38、cgroup v1；当前 runc 为 1.1.8、containerd 为 1.6.22.15.oe2403。迁移组件仍要求 containerd 2.1+ 的 2.x 系列。

## 包来源与核对范围

- CRIU 为 RISC-V SIG 普通 `25.09/mainline` 仓库的 `criu-4.1-1.oe2509.riscv64.rpm`，没有使用 `25.09-RVA23` 仓库。它不是 openEuler 24.03 原生发行包。
- 其余依赖选自 `24.03/mainline`，不提供 glibc、内核或 containerd 的替换包。
- RPM 已逐一对照对应仓库的 primary 元数据校验 SHA256；压缩及解压的 primary 元数据也对照 repomd 校验。`manifest.json` 记录精确版本、仓库地址和校验值。
- CRIU ELF 为 `EM_RISCV`，其最高 GLIBC 符号要求为 `GLIBC_2.38`。已核对直接依赖的符号版本在配套 24.03 库中可用。
- 普通仓库来源、ELF 架构和符号检查不代替目标 CPU 实测。该 CRIU RPM 未保留 `.riscv.attributes`，没有据此宣称完成全部指令集兼容验证。
- 尚未在目标服务器运行 CRIU。包内检查和安装脚本的本地模拟检查不能证明目标内核支持检查点恢复。安装后必须分别执行版本和内核检查。
- 原始 RPM 保持不变；RPM 中的许可标识、上游地址、对应源码包名称和来源记录在 `PROVENANCE.json`。

## Windows 电脑：下载与上传

下载 `rv-criu-offline-openeuler24.03-riscv64.zip` 以及同名 `.sha256` 文件。它是补充包，保留服务器上的现有平台源码目录。

ZIP 小于 GitHub 普通 Git 单文件的 100 MB 上限，可以按之前的方式提交到 `https://github.com/githuber196/Paper.git`，不需要分卷。随后沿用已经可用的仓库下载与主机间传输方式，将两份文件放到工作端 `/home/lzu/`。

## 工作端 k8snode1：安装

每个 Bash 框是一条完整命令。多行命令整体复制执行；上一步失败时，先处理输出，再继续下一步。

### 1. 进入上传目录

```bash
cd "$HOME"
```

### 2. 校验压缩包

```bash
sha256sum -c rv-criu-offline-openeuler24.03-riscv64.zip.sha256
```

### 3. 解压到新的补充包目录

```bash
unzip rv-criu-offline-openeuler24.03-riscv64.zip
```

### 4. 校验包内文件

```bash
cd "$HOME/rv-criu-offline"
```

```bash
sha256sum -c SHA256SUMS
```

### 5. 查看安装计划并做 RPM 依赖预检

```bash
sudo python3 -B "$HOME/rv-criu-offline/install.py" --plan
```

这一阶段检查系统版本、glibc、每个 RPM 的 SHA256、包身份、架构、已安装能力、缺失依赖，并执行 `rpm -Uvh --test`。输出 `newPackages` 是实际需要新增的包；42 个候选 RPM 不代表会全部安装。默认只做计划检查。

脚本保留已经安装的包；发现某个已安装包缺少必要能力时停止，让管理员检查。它不自动替换旧依赖。最终 RPM 事务预检会核对版本约束及文件冲突。

### 6. 安装缺失的包

```bash
sudo python3 -B "$HOME/rv-criu-offline/install.py" --install
```

执行时再次做完整预检，随后仅将所需的缺失 RPM 交给 `dnf --disablerepo='*'`，关闭弱依赖安装，保留本机的签名检查策略。安装失败会停止。输出 `criuInstalled: true` 只表示安装及版本命令成功，`kernelChecked: false` 表示尚未核对内核。

安装器不修改 Kubernetes、containerd 配置或防火墙规则，也不重启它们。若新增 nftables RPM，其发行包自带脚本仍遵循原包行为。

### 7. 查看版本

```bash
sudo /usr/sbin/criu --version
```

### 8. 检查内核接入条件

```bash
sudo /usr/sbin/criu check -v4
```

```bash
printf 'CRIU 内核检查退出码：%s\n' "$?"
```

退出码命令紧接上一条执行。版本命令出现 Illegal instruction、动态库报错，或内核检查退出码非 0 时，先提供完整输出。保留现有 containerd 1.6；此时还未满足安装迁移代理的条件。

`criu check` 是迁移安装的内核依赖检查；通过后仍需以后执行独立的容器迁移功能测试。

## 后续迁移接入顺序

CRIU 接入检查通过后，核对当前 containerd 的 systemd 单元、配置文件及运行负载，准备保留现有镜像路径、CNI 和 pause 镜像的 2.1.5 配置，再安排切换。之后安装工作端 `rv-migration-proxy`、控制端 `rv-migration-controller`，并配置 SSH 与节点列表。

当前一个工作节点可以先完成节点端安装；真实跨节点迁移需要至少两个符合条件的容器节点。控制机不会因为运行了迁移控制器就自动成为第二个迁移节点。
