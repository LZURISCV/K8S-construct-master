#!/usr/bin/env bash
set -euo pipefail
task_results=${TEST_RESULTS:-./migration-test-results}
task_namespace=${TEST_NAMESPACE:-migration-test}
task_migration=${MIGRATION_NAME:-counter-move}
task_pod=$(kubectl get nmg "$task_migration" -n "$task_namespace" -o jsonpath='{.status.targetPod}')
[[ -n "$task_pod" ]]
kubectl logs -n "$task_namespace" "$task_pod" --tail=5 > "$task_results/after.jsonl"
python3 - "$task_results/before.json" "$task_results/after.jsonl" <<'PY'
import json, sys
before = json.load(open(sys.argv[1]))
after = [json.loads(s) for s in open(sys.argv[2]) if s.strip()]
assert after, '目标容器没有输出'
assert all(s['boot_id'] == before['boot_id'] for s in after), '内存中的启动标识变化，不能认定状态连续'
assert after[-1]['counter'] > before['counter'], '计数未从原进度继续'
print('通过：内存启动标识保持，计数继续增长。')
print('最近采样间隔（含采样等待，不等同于精确暂停时间）秒:', (after[-1]['time_ns'] - before['time_ns']) / 1e9)
PY
