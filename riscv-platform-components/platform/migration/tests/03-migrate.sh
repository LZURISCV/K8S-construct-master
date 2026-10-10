#!/usr/bin/env bash
set -euo pipefail
: "${TARGET_NODE:?设置 TARGET_NODE}"
export TEST_NAMESPACE=${TEST_NAMESPACE:-migration-test}
export MIGRATION_NAME=${MIGRATION_NAME:-counter-move}
task_results=${TEST_RESULTS:-./migration-test-results}
umask 077
mkdir -p "$task_results"
kubectl logs -n "$TEST_NAMESPACE" memory-counter --tail=1 > "$task_results/before.json"
python3 - <<'PY' | kubectl create -f -
import json, os
print(json.dumps({'apiVersion':'migration.riscv.io/v1alpha1','kind':'NodeMigration',
 'metadata':{'name':os.environ['MIGRATION_NAME'],'namespace':os.environ['TEST_NAMESPACE']},
 'spec':{'sourcePod':'memory-counter','targetNode':os.environ['TARGET_NODE']}}))
PY
for task_attempt in $(seq 1 600); do
  task_phase=$(kubectl get nmg "$MIGRATION_NAME" -n "$TEST_NAMESPACE" -o jsonpath='{.status.phase}')
  echo "迁移阶段: $task_phase"
  case "$task_phase" in
    Succeeded) kubectl get nmg "$MIGRATION_NAME" -n "$TEST_NAMESPACE" -o json > "$task_results/migration.json"; exit 0 ;;
    Failed|RecoveryRequired) kubectl get nmg "$MIGRATION_NAME" -n "$TEST_NAMESPACE" -o yaml; exit 1 ;;
  esac
  sleep 3
done
echo '等待超时，请查看迁移状态和控制器日志。' >&2
exit 1
