#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
[[ $# -ge 4 ]] || { echo '22-compute-realtime.sh 样本数 周期us 截止期us 计算次数 [CPU编号|-1] [FIFO优先级|-1] > 结果.json'; exit 1; }
task_binary=$(mktemp)
trap 'rm -f "$task_binary"' EXIT
gcc -std=c11 -O2 -Wall -Wextra -Werror "$task_dir/realtime.c" -o "$task_binary"
"$task_binary" "$@"

