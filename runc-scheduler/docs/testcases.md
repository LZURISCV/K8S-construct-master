# 测试大纲：13 项独立执行

在控制机器的项目根目录运行。每个编号脚本只测试对应大纲用例，单独准备环境、采集证据并清理；可任意选择一项重跑，没有用例顺序依赖，也没有统一批量执行脚本。

## 公共准备与报告

```bash
export RCS_URL=http://127.0.0.1:8080
export RCS_TOKEN_FILE="$PWD/admin.token"
rcs ctl get nodes
rcs ctl get pods
```

先按 [部署文档](openeuler-riscv64.md) 安装服务及 rootfs。选定节点应 ready、未 cordon、无污点、没有其他活动容器；测试会拒绝非空闲节点。普通测试需要 demo，MPI 需要 mpi。测试之间不要同时在同一节点运行或修改其污点。

报告默认分别写入 `test-results/3-2-0001.json`～`3-2-0013.json`。包含用例 ID、起止时间、节点数量、真实容器状态、指标、日志及清理错误。重复执行默认覆盖该项报告；归档时用 `--report test-results/3-2-0001-日期.json` 指定新文件。`--help` 查看每项参数。

0001～0012 报告的 nodeCgroups 字段保留每个部署节点实际使用的 v1/v2 模式。测试逻辑兼容两者，扩缩容使用代理上报的真实资源指标；执行前可在节点运行 `rcs check-cgroups -mode v1`。

0001～0012 支持 `--url`、`--token-file`、`--ca`、`--nodes`、`--timeout` 和 `--report`；URL/token/CA 也可通过 RCS_URL、RCS_TOKEN_FILE、RCS_CA_FILE 传入。脚本只删除本次创建的工作负载，污点测试恢复原污点。失败时也尝试清理，报告先保存清理前状态。清理失败需恢复节点连接后处理报告列出的本次工作负载，保留报告用于排查。

| 状态 | 含义 | 退出码 |
|---|---|---|
| PASS | 当前用例判定满足 | 0 |
| FAIL | 前提、功能、数据或清理失败 | 1 |
| PERFORMANCE_NOT_MET | 数值正确，但性能阈值或限额影响未满足 | 1 |
| PARTIAL / SKIP | 缺少部分设备或公开贡献证据 | 2 |

一台控制机器加一台工作机器仅有一个部署节点。以下单节点命令均用 worker1；增加工作机器后用 `--nodes worker1,worker2`，或者控制机器也安装代理后用其真实节点 ID。

## 3-2-0001：自动扩容

```bash
python3 testcases/01_hpa_expand.py --nodes worker1
```

独立创建一个启用内存 autoscaler 的副本，对已启动容器开启 160MiB 内存压力，对新增副本继续开启压力。验证真实内存指标有效、期望副本与 Running 副本从 1 增至 3。只验证扩容，结束后删除测试工作负载。

## 3-2-0002：自动缩容

```bash
python3 testcases/02_hpa_shrink.py --nodes worker1
```

自行创建三个副本并施加压力，确认高内存指标，再启用 autoscaler 和关闭压力。经过缩容稳定窗口后，期望副本与 Running 副本降至 1。无需运行 0001。

## 3-2-0003：亲和性

```bash
python3 testcases/03_pod_affinity.py --nodes worker1
# 多节点更能区分“规则选中”与“只有一个可用节点”：
python3 testcases/03_pod_affinity.py --nodes worker1,worker2
```

创建带唯一标签的 anchor，再通过 podAffinity 创建 follower，核对两者在同节点。原大纲此项步骤使用 nodeAffinity，本脚本额外核对按节点标签匹配的容器位置。每项结果均保留容器 ID 和 nodeId。

## 3-2-0004：反亲和性

```bash
python3 testcases/04_anti_affinity.py --nodes worker1
# 两个部署节点时完成跨节点验证：
python3 testcases/04_anti_affinity.py --nodes worker1,worker2
```

原大纲此项的步骤为节点标签不匹配后 Pending；脚本先验证该步骤，再验证真正的 podAntiAffinity。

- 一个工作节点：首副本运行、第二副本持续 Pending；跨节点分散标记 SKIP，整项 PARTIAL。
- 两个工作节点：两副本分别位于不同节点，第三副本持续 Pending；全部满足则 PASS。

不会把一台部署设备的结果作为跨设备分散通过。

## 3-2-0005：优先级抢占

```bash
python3 testcases/05_preemption.py --nodes worker1
```

低优先级容器请求该节点约 70% 可分配 CPU，再提交相同请求的高优先级容器。验证低优先级受害者已确认 Removed 后，高优先级容器 Running，低优先级工作负载不能同时恢复为 Running。节点必须空闲。

## 3-2-0006：污点、无容忍度

```bash
python3 testcases/06_taint_without_toleration.py --nodes worker1
```

添加本次专用 NoSchedule 污点，创建没有 tolerations 的容器，验证持续 Pending。清理容器并恢复原污点。这项只验证无容忍度行为。

## 3-2-0007：污点、有容忍度

```bash
python3 testcases/07_taint_with_toleration.py --nodes worker1
```

自行添加专用污点，为容器添加 Equal 容忍度，验证在该污点节点 Running。独立清理并恢复污点，不依赖 0006。

## 3-2-0008：MPI 广播

```bash
python3 testcases/08_mpi_broadcast.py --nodes worker1 --ranks-per-node 2
# 两个部署节点：
python3 testcases/08_mpi_broadcast.py --nodes worker1,worker2 --ranks-per-node 2
```

部署 SSH worker 和 broadcast Job；rank 0 广播 42，所有 rank 核对结果，汇总数值是否正确。只执行 `mpi/broadcast.c`，不执行规约、全收集或性能用例。

## 3-2-0009：MPI 规约

```bash
python3 testcases/09_mpi_reduce.py --nodes worker1 --ranks-per-node 2
```

各 rank 输入 rank+1，MPI_Reduce 求和；rank 0 核对 `N*(N+1)/2`。独立部署和清理，只执行 mpi/reduce.c。多设备验证同样改为 `--nodes worker1,worker2`。

## 3-2-0010：MPI 全收集

```bash
python3 testcases/10_mpi_allgather.py --nodes worker1 --ranks-per-node 2
```

MPI_Allgather 收集各 rank 输入；所有 rank 验证完整数组 1～N，报告数值结果。独立部署和清理，只执行 mpi/allgather.c。

0008～0010 报告标记 communicationScope。单节点多 rank 是单设备多进程验证；选择两台真实部署节点才是跨设备验证。辅助集合通信仅用于汇总校验状态，不代表运行其他编号用例。

## 3-2-0011：计算通信重叠及资源约束

```bash
python3 testcases/11_mpi_overlap.py --nodes worker1 --ranks-per-node 2 \
  --elements 1048576 --loops 2000000 --rounds 5 --min-speedup 1.05
# 建议跨设备性能测试：
python3 testcases/11_mpi_overlap.py --nodes worker1,worker2 --ranks-per-node 2 \
  --elements 1048576 --loops 2000000 --rounds 5 --min-speedup 1.05
```

在相同数据和计算量下比较“先计算再通信”与 `MPI_Iallreduce` 重叠，交替测量顺序并取多轮平均、rank 最大耗时。之后把 worker CPU 限额减半，重建 worker，再测量同一程序。报告保存两次原始日志、CPU 限额、串行耗时、重叠耗时和加速比。

判定为：数值正确；基线加速比达到 min-speedup；减半限额后的重叠耗时高于基线。1.05 为当前脚本默认参数，可按验收约定调整；大纲未给固定百分比。未满足性能条件即 PERFORMANCE_NOT_MET，不伪造提速。调参需记录 elements、loops、rounds、网卡和设备数量，单设备结果不能作为跨设备性能结论。

MPI 参数 `--interface eth0` 或网段 CIDR 用于指定通信网卡；`--timeout 1800` 放宽 worker rootfs 准备等待，`--job-timeout 1800` 放宽 Job 执行等待。

## 3-2-0012：节点亲和性

```bash
python3 testcases/12_node_affinity.py --nodes worker1
```

使用 nodeAffinity 的 In 表达式选择 worker1，验证 Running 位置，再提交匹配不存在节点 ID 的规则，验证持续 Pending。报告保留正、反例。

## 3-2-0013：交付与公开贡献证据

原始交付包解压后、修改配置或重新编译前可执行完整文件校验：

```bash
python3 testcases/13_delivery_and_contribution.py
```

验证 MANIFEST.sha256、riscv64 二进制和两份文档。文件已配置修改或重新编译时，使用另一个原始解压目录：`--root /path/to/clean/runc-scheduler`。未提供公开贡献证据时，交付 PASS、贡献 SKIP、整项 PARTIAL。

将真实公开提交的 commit 和 HTTPS 链接填入 examples/contribution-evidence.json；在包含这些提交及项目源码的 Git 仓库执行：

```bash
python3 testcases/13_delivery_and_contribution.py --repo /path/to/git-repo \
  --evidence /path/to/contribution-evidence.json
```

辅助工具按约定目标 Go 文件的当前 HEAD 非空行、git blame 与证据所列 commit 统计比例。报告为 3-2-0013.json，并附独立 attribution 文件。比例低于 50% 记录 FAIL；本地比例达到 50% 仍需验收方核实公开链接、提交状态和统计范围，因此保留 PARTIAL。生成源码及本地统计不能自动证明已完成公开社区贡献。
