#!/usr/bin/env bash
# Run on a native RISC-V Linux host with a working Bazel C++ toolchain.
set -euo pipefail
[[ $# -eq 2 ]] || { echo 'build-proxy.sh /opt/src/istio-proxy /opt/rv-build'; exit 1; }
[[ $(uname -m) == riscv64 ]] || { echo '需要 RISC-V Linux C++ 编译环境'; exit 1; }
task_source=$(readlink -f "$1")
mkdir -p "$2"
task_output=$(readlink -f "$2")
cd "$task_source"
git rev-parse HEAD > "$task_output/proxy.source-commit"
git submodule update --init --recursive
# The source/toolchain must support riscv64; no substitution with a generic Envoy.
bazel build -c opt //src/envoy:envoy
install -m 0755 bazel-bin/src/envoy/envoy "$task_output/envoy"
"$task_output/envoy" --version
echo '运行成功后再制作 proxyv2 镜像。若上游依赖不支持 riscv64，此步骤会报错并保留诊断。'

