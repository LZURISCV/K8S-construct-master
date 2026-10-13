#!/usr/bin/env bash
# Build on another machine with Go >=1.23; the server does not need this compiler.
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
task_source=${1:?用法: build.sh /已校验的containerd-v2.1.5源码目录}
export GOTOOLCHAIN=local
python3 "$task_dir/patch_containerd.py" "$task_source"
mkdir -p "$task_dir/bin"
cd "$task_source"
go test -mod=readonly -v internal/cri/server/rv_restore_image_cache.go internal/cri/server/rv_restore_image_cache_test.go
CGO_ENABLED=0 GOOS=linux GOARCH=riscv64 go build -mod=readonly -trimpath \
  -ldflags='-s -w -X github.com/containerd/containerd/v2/version.Version=v2.1.5-rv1 -X github.com/containerd/containerd/v2/version.Revision=fcd43222d6b07379a4be9786bda52438f0dd16a1+rv-local-cache' \
  -o "$task_dir/bin/containerd" ./cmd/containerd
cd "$task_dir/bin"
sha256sum containerd > SHA256SUMS
