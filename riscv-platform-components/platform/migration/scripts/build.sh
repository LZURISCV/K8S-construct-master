#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$task_dir/../scripts/go-env.sh"
mkdir -p "$task_dir/bin"
cd "$task_dir/runtime"
go mod download
CGO_ENABLED=0 GOOS=linux GOARCH="${GOARCH:-riscv64}" go build -mod=readonly -trimpath -ldflags='-s -w' -o "$task_dir/bin/rv-migrate-runtime" .
(
  cd "$task_dir/bin"
  sha256sum rv-migrate-runtime > SHA256SUMS
)
echo "已构建: $task_dir/bin/rv-migrate-runtime"
