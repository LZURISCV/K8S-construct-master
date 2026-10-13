#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
task_component=${1:-help}
shift || true
case "$task_component" in
  migration-build) exec bash "$task_dir/migration/scripts/build.sh" "$@" ;;
  migration-node) exec bash "$task_dir/migration/scripts/install-node.sh" "$@" ;;
  migration-controller) exec bash "$task_dir/migration/scripts/install-controller.sh" "$@" ;;
  node-credentials) exec bash "$task_dir/scripts/issue-node-credentials.sh" "$@" ;;
  resource|network-host|scenario) exec bash "$task_dir/control/install.sh" "$@" ;;
  node-agent) exec bash "$task_dir/node/install.sh" "$@" ;;
  device-node) exec bash "$task_dir/device-plugin/install.sh" "$@" ;;
  collective|fusion) exec bash "$task_dir/communication/build.sh" "$@" ;;
  ecosystem) exec python3 -B "$task_dir/ecosystem/install.py" "$@" ;;
  validation) exec bash "$task_dir/validation/prepare.sh" "$@" ;;
  all) exec bash "$task_dir/integrate.sh" "$@" ;;
  check) exec python3 -B "$task_dir/check.py" "$@" ;;
  *)
    cat <<'EOF'
现有集群构建完成后，按角色安装附加组件：
  bash platform/install.sh migration-build
  bash platform/install.sh migration-node
  bash platform/install.sh migration-controller /绝对路径/config.json
  bash platform/install.sh node-credentials 节点名 节点IPv4 https://控制机IP:6443 root
  bash platform/install.sh resource /绝对路径/platform.json
  bash platform/install.sh node-agent
  bash platform/install.sh device-node /绝对路径/devices.json
  bash platform/install.sh network-host /绝对路径/platform.json
  bash platform/install.sh collective
  bash platform/install.sh fusion
  bash platform/install.sh ecosystem /绝对路径/ecosystem.json
  bash platform/install.sh validation
  bash platform/install.sh all platform.json migration.json ecosystem.json
  bash platform/install.sh check
全部组件手册: docs/installation-and-usage.md；功能对比: docs/comparison.md。
EOF
    ;;
esac
