#!/usr/bin/env bash
# Submit only the same-node recovery request; source creation and verification
# remain separate steps in 02-create-counter.sh and 04-verify-memory.sh.
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export TEST_NAMESPACE=${TEST_NAMESPACE:-migration-test}
export MIGRATION_NAME=${MIGRATION_NAME:-counter-local}
export MIGRATION_MODE=SameNode
export TARGET_NODE
TARGET_NODE=$(kubectl get pod memory-counter -n "$TEST_NAMESPACE" -o jsonpath='{.spec.nodeName}')
[[ -n "$TARGET_NODE" ]] || { echo '源 Pod 尚未绑定节点' >&2; exit 1; }
exec bash "$task_dir/03-migrate.sh"
