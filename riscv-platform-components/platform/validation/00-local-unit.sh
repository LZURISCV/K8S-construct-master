#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
python3 -B -m unittest discover -s "$task_dir/tests" -p 'test_*.py' -v

