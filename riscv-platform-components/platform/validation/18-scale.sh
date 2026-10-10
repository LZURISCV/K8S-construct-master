#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
[[ $# -ge 1 ]] || { echo '18-scale.sh --image 镜像 --pods 数量 --output 结果.json'; exit 1; }
python3 -B "$task_dir/scale.py" "$@"

