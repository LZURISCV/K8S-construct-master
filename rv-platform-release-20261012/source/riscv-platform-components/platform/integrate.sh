#!/usr/bin/env bash
# Control-host integration after role-specific node setup and image preparation.
set -euo pipefail
[[ $EUID -eq 0 && $# -eq 3 ]] || { echo 'root: integrate.sh platform.json migration.json ecosystem.json'; exit 1; }
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
bash "$task_dir/control/install.sh" "$1"
bash "$task_dir/migration/scripts/install-controller.sh" "$2"
python3 -B "$task_dir/ecosystem/install.py" "$3"
python3 -B "$task_dir/check.py"
echo '控制机安装步骤完成；节点安装及独立用例的结果需要按手册检查。'

