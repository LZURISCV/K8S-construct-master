# openEuler 22 系列 / RISC-V 编译与运行

本文按两台机器均为 riscv64 编写。小版本由 `/etc/os-release` 确认，DNF 使用服务器已有的匹配软件源，示例 IP 替换为实际地址。

本版支持 cgroup v1/v2，默认自动检测。按你现有的 **cgroup v1** 环境运行即可，下面的节点示例使用 cgroupfs 管理方式。

| 设备 | 示例地址 | 角色 |
|---|---|---|
| 控制机器 | 192.168.10.10 | 控制服务、CLI、Python 测试 |
| 容器部署机器 | 192.168.10.11 | worker1、代理、runc |
| 后续新增设备 | 192.168.10.12 | worker2；其他设备同理 |

纯控制机器不计为部署节点。当前两台设备对应一个工作节点；若给控制机器另装独立 ID 的代理，也可以形成两个真实部署节点。

## 1. 解压与环境检查

将交付包复制到两台机器，例如解压到 `~/rcs-work`：

```bash
sudo dnf install -y unzip
mkdir -p ~/rcs-work
cd ~/rcs-work
unzip /path/to/runc-scheduler-v0.1.2.zip
cd runc-scheduler
sha256sum -c MANIFEST.sha256
uname -m
cat /etc/os-release
```

应为 riscv64、openEuler。基础依赖按角色安装：

```bash
# 控制机器
sudo dnf install -y python3 unzip coreutils file
bash scripts/check-openeuler-riscv64.sh control

# 部署机器
sudo dnf install -y runc python3 coreutils util-linux file
bash scripts/check-openeuler-riscv64.sh agent v1
command -v runc
runc --version
```

DNF 找不到 runc 时检查 `dnf repolist` 及 RISC-V 源是否与本系统版本匹配。使用匹配 openEuler 22 系列的 RISC-V 源或已有 riscv64 runc 包，不混用其他架构的 RPM。需要支持当前 cgroup 模式和 OCI 资源限制的 runc，实际版本用 runc --version 查看。

### cgroup v1 环境检查

```bash
findmnt -t cgroup,cgroup2
cat /proc/cgroups
./bin/rcs-linux-riscv64 check-cgroups -mode v1
```

典型 v1 布局是 /sys/fs/cgroup 为 tmpfs，cpu、memory、cpuset 等控制器分别挂载为 cgroup；cpu 和 cpuacct 也可能合并挂载。检测命令输出 version:v1 和各控制器实际挂载点。额外的 /sys/fs/cgroup/unified 子挂载会按 v1/混合布局检查，避免仅凭该目录存在误判为统一 v2。

本实现的 v1 模式检查 cpu、cpuacct、memory、pids、devices、freezer 控制器已挂载且可写，并检查 CPU/内存计数文件。cpu 支持 CFS 带宽限制，用于 CPU quota；memory 和 pids 控制器用于内存及进程上限。根层级没有部分限额文件属于正常情况，限额由 runc 在容器子 cgroup 中设置。只有 cpuset 挂载不能证明这些条件齐全，缺少控制器时命令会指出具体名称；按实际内核和系统挂载配置补齐。

样例为 cgroupMode:auto、systemdCgroup:false，使用 cgroupfs 管理。已确认 v1 时可改为 cgroupMode:v1，避免系统模式改变后静默使用其他模式。原有 systemdCgroup:true 也可用于支持它的 v1 systemd/runc 环境，该字段不代表 cgroup 版本。已有活动容器时保持原管理方式。检查只读，不修改内核参数或切换模式。详见 [cgroup v1 适配说明](cgroup-v1.md) 和 [Linux cgroup v1 文档](https://docs.kernel.org/admin-guide/cgroup-v1/cgroups.html)。

## 2. 编译与二进制

### A. 使用已提供的 RISC-V 二进制

```bash
chmod +x bin/rcs-linux-riscv64
./bin/rcs-linux-riscv64 version
sha256sum -c bin/SHA256SUMS
```

控制端和部署端都使用该文件，运行不需要 Go 或 GCC；核心以 CGO_ENABLED=0 构建。

### B. 在 RISC-V / openEuler 原生编译

需要 Go 1.22 或以上。先检查实际软件源版本：

```bash
sudo dnf install -y golang
go version
```

版本不足时，可把官方 riscv64 工具链装入专用目录。下面固定为本交付使用的 Go 1.27.1；归档与 SHA256 来自 [Go 官方发布元数据](https://go.dev/dl/?mode=json)，安装方法见 [Go 官方说明](https://go.dev/doc/install)。

```bash
sudo dnf install -y curl tar gzip
curl --fail --location --proto '=https' --tlsv1.2 \
  https://go.dev/dl/go1.27.1.linux-riscv64.tar.gz -o /tmp/go1.27.1.linux-riscv64.tar.gz
echo '62287667ee5e5f540f30fb9b7529a27fe582f22c6bfd726ece9b045f4c54ee61  /tmp/go1.27.1.linux-riscv64.tar.gz' | sha256sum -c -
# 首次安装，目标目录应为空；已有安装可直接复用
sudo mkdir -p /opt/rcs-toolchains/go1.27.1
sudo tar -C /opt/rcs-toolchains/go1.27.1 -xzf /tmp/go1.27.1.linux-riscv64.tar.gz
export PATH=/opt/rcs-toolchains/go1.27.1/go/bin:$PATH
go version
bash scripts/build-openeuler-riscv64.sh
```

脚本执行 Go 单元测试、go vet，生成 bin/rcs-linux-riscv64、bin/SHA256SUMS-riscv64、test-results/go-test.jsonl。只编译的等价命令：

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=riscv64 go build -trimpath \
  -ldflags='-s -w -X main.version=0.1.2' -o bin/rcs-linux-riscv64 .
```

### C. 在 Windows 或 x86 Linux 交叉编译

核心只有 Go 标准库，不需要 RISC-V GCC。Windows PowerShell：

```powershell
.\scripts\build.ps1 -GoExecutable 'C:\path\to\go\bin\go.exe'
```

Linux 使用 `bash scripts/build.sh`。生成的 riscv64 文件复制到服务器运行；MPI C 程序按第 6 节在 RISC-V 内编译。

## 3. 安装控制服务

在控制机器项目目录执行：

```bash
umask 077
./bin/rcs-linux-riscv64 token > admin.token
./bin/rcs-linux-riscv64 token > node.token
sudo bash scripts/install.sh control ./bin/rcs-linux-riscv64 \
  configs/controller.json admin.token node.token
sudo systemctl status rcs-control --no-pager
sudo journalctl -u rcs-control -n 50 --no-pager
export RCS_URL=http://127.0.0.1:8080
export RCS_TOKEN_FILE="$PWD/admin.token"
rcs ctl get nodes
```

首次查询为空。默认监听 0.0.0.0:8080，数据在 /var/lib/rcs-control/state.json。新终端重新设置环境变量。已有部署重新安装时复用原 token 和状态文件，不重新生成凭据。

通过 SSH/SCP 将 node.token 和交付包复制给部署机器，管理 token 留在控制端。节点主动访问控制端 8080。启用 firewalld 时，控制端按来源放行，例如：

```bash
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="192.168.10.11/32" port port="8080" protocol="tcp" accept'
sudo firewall-cmd --reload
```

新增节点或远程管理机器时，为对应来源添加规则。

## 4. 安装 worker1

在部署机器项目目录准备最小 demo rootfs：

```bash
sudo bash scripts/prepare-demo-rootfs.sh ./bin/rcs-linux-riscv64 /opt/rcs/images/demo
```

该 rootfs 包含本机架构的静态压力程序，用于调度和扩缩容，无需发行版镜像。修改 configs/worker1.json：

- id 为全体节点中唯一 ID。
- address 为本机真实、可被其他部署设备访问的 IPv4。
- controlURL 指向控制机器 IP:8080。
- runc 填入 `command -v runc` 的路径。
- images.demo 对应上述目录；首次 v1 部署使用 systemdCgroup:false，cgroupMode:auto 或 v1。
- reserve 默认预留 250m CPU 和 512MiB 内存，可按硬件调整。

```bash
sudo bash scripts/install.sh agent ./bin/rcs-linux-riscv64 configs/worker1.json node.token
sudo systemctl status rcs-agent --no-pager
sudo journalctl -u rcs-agent -n 80 --no-pager
```

控制端运行 `rcs ctl get nodes`，应出现 worker1、architecture:riscv64、cgroupMode:v1、ready:true 和 demo 镜像。capacity 是扣除 reserve 后可分配资源。

## 5. 扩展多个部署平台

在每台新机器重复第 1、4 节，复用控制地址和 node.token。以 configs/worker2.json 为模板修改 ID、IP、rootfs、标签和 reserve。控制端自动注册，节点数不固定。

要把当前控制机器同时作为第二个部署节点，在其上准备 rootfs，以新 ID（如 worker-control）、控制机器真实 IP、独立代理 dataDir 安装代理。控制和代理共用程序，分别读取 controller.json、agent.json。给控制服务预留足够资源。

## 6. MPI rootfs 与网络

0008～0011 需要 mpi rootfs，其他测试只需 demo。参与 MPI 的节点使用相同 riscv64 rootfs、Open MPI 版本和前缀。本交付固定 Open MPI 4.1.8，源码及 SHA256 来自 [官方 4.1 下载页](https://www.open-mpi.org/software/ompi/v4.1/)。SSH 启动要求免交互认证及统一路径，见 [Open MPI SSH 说明](https://docs.open-mpi.org/en/v5.0.8/launching-apps/ssh.html)。

在一台 RISC-V 部署机器构建：

```bash
sudo dnf install -y curl coreutils util-linux
# 使用新目录，建议预留数 GiB 空间；原生编译可能较慢
sudo bash scripts/create-openeuler-mpi-rootfs.sh /opt/rcs/images/mpi
# VERSION_ID 与 DNF releasever 不一致时显式指定匹配版本，例如：
# sudo bash scripts/create-openeuler-mpi-rootfs.sh /opt/rcs/images/mpi 22.03
```

脚本从本机配置的软件源用 DNF installroot 安装 gcc、OpenSSH 等，再在 rootfs 内编译 MPI 到 /opt/openmpi。参数含义见 [DNF 官方文档](https://dnf.readthedocs.io/en/latest/command_ref.html)。保持源签名检查，缺包时先检查本机 RISC-V 源。编译默认并发 2，可用 `sudo env RCS_BUILD_JOBS=4 bash ...` 调整。

构建目录和源码包保留在 rootfs/tmp 便于排查。成功后可删除 `/opt/rcs/images/mpi/tmp/openmpi-4.1.8` 和 `/opt/rcs/images/mpi/tmp/openmpi-4.1.8.tar.gz` 这两个确切路径以减少容器拷贝量。脚本退出时卸载临时绑定的 dev、proc，打包前用 findmnt 确认目录下无挂载。

在控制端创建一次测试密钥，安全复制到各部署节点：

```bash
umask 077
ssh-keygen -t ed25519 -N '' -f mpi.key
```

每个部署节点执行：

```bash
sudo bash scripts/prepare-mpi-rootfs.sh /opt/rcs/images/mpi ./bin/rcs-linux-riscv64 mpi.key
```

脚本识别 /opt/openmpi、/usr/lib64/openmpi 等前缀，分别编译广播、规约、全收集和重叠程序，安装 launcher 与 SSH 配置。也可以准备一份后保留所有者、权限复制整个 rootfs 到其他同架构节点。节点路径和软件前缀保持一致。

修改正在使用的 `/etc/rcs/agent.json`，保留 demo 并添加 mpi：

```json
"images": {
  "demo": "/opt/rcs/images/demo",
  "mpi": "/opt/rcs/images/mpi"
}
```

```bash
sudo systemctl restart rcs-agent
# 在控制端确认节点上报了 mpi
rcs ctl get nodes
```

测试在每个部署节点启动一个监听宿主机 TCP 2222 的 SSH worker，再创建 launcher Job。节点间需要 2222 和 MPI 动态 TCP 端口互通。实验网 firewalld 可按对端部署 IP 放行 TCP，例如 worker1 放行 worker2：

```bash
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="192.168.10.12/32" protocol value="tcp" accept'
sudo firewall-cmd --reload
```

worker2 反向放行 worker1。单个部署节点的多进程测试不能代表跨设备通信。多网卡加测试参数 `--interface eth0` 或通信子网 CIDR。SSH 密钥仅用于该批测试容器，测试 rootfs 的密码认证关闭。

## 7. 测试和排查

按 [逐项测试文档](testcases.md) 在控制端独立执行。节点需空闲且无污点；普通测试至少有 500m 可分配 CPU、1GiB 内存，MPI 至少 750m 和 1GiB。MPI rootfs 拷贝慢时增加 `--timeout 1800`，Job 用 `--job-timeout` 调整。

| 现象 | 检查 |
|---|---|
| 执行格式错误 | uname -m、file bin/rcs-linux-riscv64；rootfs 也需 riscv64 |
| 节点不出现 | IP:8080 连通性、controlURL、node.token、两侧 journalctl |
| cgroup 错误 | check-cgroups 输出、控制器挂载和内核配置、cgroupMode、systemdCgroup、runc 版本 |
| Pending | get pods 的 reason、get events；资源、标签、污点、镜像、端口 |
| Failed | 容器日志、代理日志、rootfs 依赖和权限 |
| MPI 启动失败 | 2222、TCP 路由、软件版本/前缀、密钥、网卡选择 |
| MPI 性能不达标 | 保存原始计时，调整 elements/loops/rounds，按实测报告结果 |

默认 cgroupfs 配置查看 runtime：`sudo runc --root /run/rcs-runc list`。若实际配置 systemdCgroup:true，则补上 --systemd-cgroup。SELinux 启用时检查 AVC 拒绝日志并配置所需策略，脚本不自动关闭 SELinux。报告保留清理前状态、日志和错误，先保存再排查。

## 8. 验证边界

开发环境已执行源码测试和 riscv64 交叉编译。尚未连接你的服务器，真实 runc 容器、内核资源限制和 MPI 性能需通过本包独立测试确认。官方 Go/MPI 校验值已核对，软件源包可用性及该设备内核设置以实机检查为准。
