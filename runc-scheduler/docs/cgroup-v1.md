# cgroup v1 适配说明（0.1.2）

针对现有 RISC-V / openEuler 的 cgroup v1 环境，0.1.2 已修改运行时、安装检查和文档。上一版只允许 v2，因此旧二进制需要替换。

## 1. 版本与控制器检测

```bash
chmod +x bin/rcs-linux-riscv64
./bin/rcs-linux-riscv64 version
./bin/rcs-linux-riscv64 check-cgroups -mode v1
findmnt -t cgroup,cgroup2
cat /proc/cgroups
```

预期程序版本 0.1.2，检测输出 version:v1 和控制器实际挂载点。判断依据为 /proc/self/mountinfo 中的文件系统类型及控制器挂载选项，支持 cpu,cpuacct 合并挂载和挂载点转义。只有 /sys/fs/cgroup 本身挂载为 cgroup2 才选统一 v2；单独的 unified 子挂载按 v1/混合布局处理。

v1 需要 cpu、cpuacct、memory、pids、devices、freezer 控制器已挂载且可写。cpuacct.usage、memory.usage_in_bytes 用于指标采集。CPU quota 要求内核支持 CONFIG_CFS_BANDWIDTH；启动真实容器时 runc 应用 CPU、内存、PID 限额，失败则容器报告 Failed，不继续作为成功实例。

根层级的 pids.max、freezer.state 等文件可能不暴露，检查程序不会把这种情况当成控制器缺失。只有 cpuset 挂载、或者只有 /proc/cgroups 显示 enabled:1，均不足以证明所需控制器已经挂载。

检测缺失时按具体错误排查。例如 memory 未挂载，先检查 /proc/cgroups、/proc/cmdline、内核 CONFIG_MEMCG 及系统挂载配置；cpu 有挂载但创建时报 quota 文件缺失，则检查 CONFIG_CFS_BANDWIDTH。按当前 openEuler/设备的启动方式修复，检查工具不自动改启动配置或重新挂载已有层级。

## 2. 节点配置

首次部署、按本包示例使用 cgroupfs：

```json
"cgroupMode": "v1",
"systemdCgroup": false
```

cgroupMode 默认为 auto，可填写 v1/v2；显式版本与实际环境不符时启动失败并显示原因。各部署节点可以分别使用 v1/v2，上报给控制端的 cgroupMode 为实际检测结果。

systemdCgroup 单独决定 runc 的管理方式：false 使用 cgroupfs、OCI 路径为 /rcs/容器ID；true 使用 --systemd-cgroup 和 rcs.slice 下的 scope。这两种方式与 cgroup 版本分别配置。现有服务已经用 true 时，更新程序保留该配置；不要在仍有活动容器时更换管理方式。

## 3. 资源限额和指标

OCI 的 cpu.quota/cpu.period、memory.limit、pids.limit 由 runc 转为对应内核控制器设置，参见 [OCI Linux 资源规范](https://github.com/opencontainers/runtime-spec/blob/main/config-linux.md)。

v1 指标直接读取容器的真实 cgroup 文件：先通过 runc state 取得 init PID，再从 /proc/PID/cgroup 确定 cpuacct 和 memory 的组路径，结合挂载信息读取 cpuacct.usage、memory.usage_in_bytes。CPU 累计时间单位为纳秒，按两次采样的时间差换算 CPU 毫核；内存单位为字节。来源见 [Linux CPU 计数文档](https://docs.kernel.org/admin-guide/cgroup-v1/cpuacct.html) 和 [内存控制器文档](https://docs.kernel.org/admin-guide/cgroup-v1/memory.html)。

路径解析同时支持 cgroupfs、systemd scope、合并控制器挂载和挂载根目录。采样首帧、文件缺失、计数重置、无进程组信息时不标记有效指标，避免错误数据导致缩容。v2 保留 runc events --stats 采集方式。

在 worker 上查看运行容器的实际 v1 限额（以下示例为 cgroupfs，容器 ID 来自控制端 get pods）：

```bash
RCS_TEST_POD=rcs-替换为实际ID
sudo runc --root /run/rcs-runc state "$RCS_TEST_POD"
# 从 state 的 pid 字段获取 PID 后：
cat /proc/实际PID/cgroup
# CPU 控制器若合并挂载在 cpu,cpuacct：
cat "/sys/fs/cgroup/cpu,cpuacct/rcs/$RCS_TEST_POD/cpu.cfs_period_us"
cat "/sys/fs/cgroup/cpu,cpuacct/rcs/$RCS_TEST_POD/cpu.cfs_quota_us"
cat "/sys/fs/cgroup/memory/rcs/$RCS_TEST_POD/memory.limit_in_bytes"
cat "/sys/fs/cgroup/pids/rcs/$RCS_TEST_POD/pids.max"
```

实际目录按 check-cgroups 和 /proc/PID/cgroup 输出替换；systemd 模式路径通常为 rcs.slice/rcs-编号.scope。period 为 100000 微秒，quota 为 limits.cpuMillis×100，内存上限等于 limits.memoryBytes，PID 上限为 4096。

## 4. 更新已有安装

新部署直接按 [openEuler 部署文档](openeuler-riscv64.md) 执行。已有安装先解压新包到另一个目录，保留原配置、token 和状态；不覆盖正在使用的可执行文件。

```bash
# 部署节点：先检查新程序支持当前环境
./bin/rcs-linux-riscv64 check-cgroups -mode v1
sudo systemctl stop rcs-agent
sudo install -m 0755 ./bin/rcs-linux-riscv64 /usr/local/bin/rcs
# 在 /etc/rcs/agent.json 增加 cgroupMode:v1，保留原 systemdCgroup 和其他字段
sudo systemctl start rcs-agent
sudo journalctl -u rcs-agent -n 80 --no-pager
```

控制端更新：停止 rcs-control，安装对应架构的新程序，保留 /var/lib/rcs-control/state.json 与 /etc/rcs 下的原配置/凭据，再启动。控制和代理在同机时，替换共用程序前停止两项服务，替换后启动两项服务。代理的 KillMode=process 会保留已有容器，重启后根据本地状态恢复协调。

demo rootfs 中的压力工具行为未变化，可继续使用；如需整个 rootfs 内也显示 0.1.2，准备一个新 demo 目录并更新代理 images.demo。

## 5. 独立测试

测试仍是 [13 个独立入口](testcases.md)，没有合并成批量脚本。先选空闲节点逐项运行扩容、缩容、抢占、污点等，再按已准备的 MPI 环境逐项运行通信测试。报告记录实际节点 cgroupMode、容器指标和状态。

本地测试已覆盖 v1/v2 检测、混合和合并挂载、缺失/只读控制器、显式模式不匹配、v1 路径映射及指标单位。未连接你的服务器，真实资源限额、容器创建和性能仍需在实机执行。
