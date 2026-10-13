#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
: "${1:?传入本用例生成的 report.json 的完整路径}"
exec python3 -B "$task_dir/../support.py" cleanup "$1"
