# 完整包：先看这里

本包用于 openEuler 22 系列、RISC-V（riscv64）、cgroup v1。支持一台控制机和多台执行节点。网络采用 hostNetwork + TCP；迁移采用已确认的停顿式有状态方案。

## 1. 包内内容

| 内容 | 位置 |
|---|---|
| 项目说明和组件入口 | [README.md](README.md) |
| 完整安装、编译、配置和使用步骤 | [installation-and-usage.md](platform/docs/installation-and-usage.md) |
| 详细源码讲解 | [source-code-guide.md](platform/docs/source-code-guide.md) |
| 与已有 K8s 组件的功能比较 | [comparison.md](platform/docs/comparison.md) |
| 集成顺序和组件接口 | [component-integration.md](platform/docs/component-integration.md) |
| 停顿式迁移专门手册 | [迁移手册](platform/migration/README.md) |
| 已执行的本地检查和实机待测项 | [验证记录](platform/VERIFICATION.md) |
| 原基础集群构建脚本 | 根目录 k8s_master.sh、k8s_node.sh、k8s_master_other_components.sh |
| 平台源码、配置示例和安装脚本 | platform/ 下各组件目录 |
| 独立测试脚本及报告工具 | platform/validation/、platform/migration/tests/ |
| 逐文件 SHA256 校验清单 | 根目录 SHA256SUMS |

这些文件都在同一个 ZIP 中，文档使用相对链接，不需要再下载上一轮文件或访问原电脑上的源码目录。

## 2. 拷到另一台 Linux 机器

把完整 riscv-platform-components.zip 上传到目标控制机的 /tmp/。可以通过自己的文件传输工具或 scp 上传。每个 Bash 块分别执行；如果已经有部署，先阅读完整手册第 0.2 和 6.5 节，正常释放旧版直通任务后再升级。

下面按新目录安装，先准备解压工具：

~~~bash
sudo dnf install -y unzip
~~~

在当前用户的工作目录解压。如果当前目录已经有 riscv-platform-components，请先换到一个空的工作目录，避免混入旧文件：

~~~bash
unzip /tmp/riscv-platform-components.zip
~~~

进入源码目录并校验所有文件：

~~~bash
cd riscv-platform-components
~~~

~~~bash
sha256sum -c SHA256SUMS
~~~

校验应全部为 OK；应在修改配置示例之前执行。修改后的配置文件与原校验值不同属于预期。ZIP 也自带 CRC 校验；SHA256SUMS 用于确认解压后的文件完整。

完整手册统一使用 /opt/rv-platform-src。确认这个路径尚不存在后，把刚解压的目录移过去。下面的命令在路径已存在时停止，不覆盖已有安装：

~~~bash
test ! -e /opt/rv-platform-src && sudo mv -T "$PWD" /opt/rv-platform-src
~~~

~~~bash
cd /opt/rv-platform-src
~~~

接着打开 platform/docs/installation-and-usage.md，从第 1 节依次执行。已有 K8s 集群走第 2.1 节；新建集群走第 2.2 节。按每条命令标明的控制机/Worker 角色执行，不要在每台机器上重复安装控制面。

各 Worker 同样需要本包源码，解压并放到相同的 /opt/rv-platform-src；只执行手册中属于 Worker 的步骤。先替换示例节点名、IP、镜像仓库和实际共同网段，再创建任务。控制器不限制执行节点为两台。

## 3. 编译和依赖

包内包含自有组件源码、构建脚本、Dockerfile、Helm chart、配置示例及用 Go 1.21 构建的 RISC-V 迁移工具，以及 containerd 2.1.5 的 RISC-V 运行程序。Python 控制器无需编译；自有迁移工具/设备插件可用现有 Go 1.21 构建，不需要安装 1.27，脚本禁止工具链自动升级。MPI 程序、通信镜像及生态组件的构建步骤在完整手册中。第三方生态源码如要求更高 Go，使用匹配的预编译产物或在其他构建机准备，服务器 Go 保持 1.21。

这是源码与文档交付包。系统 RPM、Go 模块缓存、第三方项目源码和容器镜像需要按手册从可用的软件源/仓库准备；这些依赖没有全部打进 ZIP。因此首次安装需要能访问相应仓库，隔离网络环境需提前准备依赖和镜像。

迁移运行时可按包内源码重新构建。通信镜像请新构建为 0.2.0，不使用旧版缓存。跨执行节点迁移和网络对照需要至少两个 Worker；一台控制机加一台 Worker 可以先完成安装与节点内功能检查。

## 4. 测试入口

测试按功能拆分，分别运行 platform/validation/01～22 的脚本；迁移测试单独在 platform/migration/tests/。具体环境变量、执行顺序、结果保存和报告生成见完整手册第 11 节。先完成依赖、集群和组件安装，再运行实机用例。

本包已完成源码侧检查，尚未在你的 RISC-V 集群完成实机联调。请保存真实原始结果，再判断吞吐、迁移和规模指标。
