#!/usr/bin/env bash
# Default: verify bundled RISC-V binaries; no compiler or runtime switch.
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
task_version=v2.1.5
task_output=${CONTAINERD_OUTPUT_DIR:-$task_dir/containerd-bin}
if [[ ${CONTAINERD_BUILD_FROM_SOURCE:-0} != 1 ]]; then
  for task_file in containerd containerd-shim-runc-v2 ctr SHA256SUMS SOURCE_COMMIT; do
    [[ -f "$task_output/$task_file" ]] || { echo "缺少预编译文件：$task_output/$task_file" >&2; exit 1; }
  done
  (cd "$task_output"; sha256sum -c SHA256SUMS)
  echo '已校验附带的 Linux/riscv64 containerd 2.1.5；服务器无需升级 Go。尚未安装或切换运行时。'
  exit 0
fi
source "$task_dir/../scripts/go-env.sh"
if [[ "$task_go_version" =~ ^go1\.([0-9]+) ]] && (( BASH_REMATCH[1] < 23 )); then
  echo '上游 containerd 2.1.5 源码要求 Go 1.23+。服务器 Go 1.21 请使用包内预编译程序；需重建时在另一台构建机执行，不会自动安装 Go。' >&2
  exit 1
fi
command -v git >/dev/null
task_source=${CONTAINERD_SOURCE_DIR:-/usr/local/src/rv-platform/containerd-v2.1.5}
if [[ ! -d "$task_source/.git" ]]; then
  mkdir -p "$(dirname -- "$task_source")"
  git clone --depth 1 --branch "$task_version" https://github.com/containerd/containerd.git "$task_source"
fi
[[ $(git -C "$task_source" describe --tags --exact-match) == "$task_version" ]] || { echo '源码标签不符合要求' >&2; exit 1; }
[[ -z $(git -C "$task_source" status --porcelain) ]] || { echo 'containerd 源码存在本地修改，请先检查' >&2; exit 1; }
mkdir -p "$task_output"
task_commit=$(git -C "$task_source" rev-parse HEAD)
cd "$task_source"
for task_command in containerd containerd-shim-runc-v2 ctr; do
  CGO_ENABLED=0 GOOS=linux GOARCH="${GOARCH:-riscv64}" go build -mod=readonly -trimpath \
    -ldflags="-s -w -X github.com/containerd/containerd/v2/version.Version=$task_version -X github.com/containerd/containerd/v2/version.Revision=$task_commit" \
    -o "$task_output/$task_command" "./cmd/$task_command"
done
printf '%s\n' "$task_commit" > "$task_output/SOURCE_COMMIT"
cd "$task_output"
sha256sum containerd containerd-shim-runc-v2 ctr > SHA256SUMS
echo "已构建至 $task_output；尚未安装或切换运行时。"
