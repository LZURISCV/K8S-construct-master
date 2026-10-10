#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
python3 -B "$task_dir/cases.py" taint-reject

