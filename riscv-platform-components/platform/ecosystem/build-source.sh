#!/usr/bin/env bash
# Sources must already be checked out to chosen immutable versions.
set -euo pipefail
[[ $# -eq 3 ]] || { echo 'build-source.sh helm|prometheus|grafana|istio-go SOURCE OUTPUT'; exit 1; }
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
  grafana)
    [[ $(uname -m) == riscv64 ]] || { echo 'Grafana 完整构建请在 RISC-V Linux 执行'; exit 1; }
    corepack enable
    yarn install --immutable
    make build-go
    yarn build
    task_binary=$(find bin -type f -name grafana -path '*riscv64*' | head -n1)
    [[ -n "$task_binary" ]] || { echo '上游版本输出路径变化，检查 bin 目录'; exit 1; }
    install -m 0755 "$task_binary" "$task_output/grafana"
    cp -a public conf "$task_output/" ;;
  istio-go)
    CGO_ENABLED=0 GOOS=linux GOARCH=riscv64 go build -trimpath -o "$task_output/pilot-discovery" ./pilot/cmd/pilot-discovery
    CGO_ENABLED=0 GOOS=linux GOARCH=riscv64 go build -trimpath -o "$task_output/pilot-agent" ./pilot/cmd/pilot-agent
    CGO_ENABLED=0 GOOS=linux GOARCH=riscv64 go build -trimpath -o "$task_output/istioctl" ./istioctl/cmd/istioctl ;;
  *) echo '未知构建目标'; exit 1 ;;
esac
echo '源码构建完成。Envoy 必须另用匹配的 istio/proxy 源码和 RISC-V 工具链编译，不能由 Go 产物替代。'

