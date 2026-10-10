#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 -B -m unittest discover -s "$task_dir/tests" -p 'test_*.py' -v
cd "$task_dir/runtime"
go test -v ./...
