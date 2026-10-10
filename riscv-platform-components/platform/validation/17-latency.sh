#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
[[ $# -ge 1 ]] || { echo '17-latency.sh --host 实际服务器地址 --deadline-ms 实际截止期 --output 结果.json'; exit 1; }
python3 -B "$task_dir/latency.py" client "$@"

