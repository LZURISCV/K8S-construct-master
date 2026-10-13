#!/usr/bin/env bash
# Sources must already be checked out to chosen immutable versions.
set -euo pipefail
[[ $# -eq 3 ]] || { echo 'build-source.sh helm|prometheus SOURCE OUTPUT'; exit 1; }
task_kind=$1
task_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
source "$task_dir/scripts/go-env.sh"
task_source=$(readlink -f "$2")
mkdir -p "$3"
task_output=$(readlink -f "$3")
cd "$task_source"
git rev-parse HEAD > "$task_output/$task_kind.source-commit"
case "$task_kind" in
  helm)
    CGO_ENABLED=0 GOOS=linux GOARCH=riscv64 go build -trimpath -o "$task_output/helm" ./cmd/helm ;;
  prometheus)
    # Native build includes upstream UI assets. Node/npm version follows source package.json.
    [[ $(uname -m) == riscv64 ]] || { echo 'Prometheus 完整 UI 构建请在 RISC-V Linux 执行'; exit 1; }
    make build
    install -m 0755 prometheus promtool "$task_output/" ;;
  *) echo '未知构建目标'; exit 1 ;;
esac
echo '源码构建完成。'

