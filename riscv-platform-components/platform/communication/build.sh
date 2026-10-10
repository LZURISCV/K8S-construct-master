#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
if ! command -v mpicc >/dev/null; then
  for task_path in /usr/lib64/openmpi/bin /usr/lib/openmpi/bin; do
    if [[ -x "$task_path/mpicc" ]]; then export PATH="$task_path:$PATH"; break; fi
  done
fi
command -v mpicc >/dev/null || { echo '安装 gcc make openmpi openmpi-devel，并把 mpicc/mpirun 加入 PATH'; exit 1; }
mkdir -p "$task_dir/bin"
mpicc -std=c11 -O3 -Wall -Wextra -Werror "$task_dir/rv_collective.c" "$task_dir/benchmark.c" -lm -o "$task_dir/bin/rv-bench"
(cd "$task_dir/bin"; sha256sum rv-bench > SHA256SUMS)
mpirun --version
echo '已生成 communication/bin/rv-bench'

