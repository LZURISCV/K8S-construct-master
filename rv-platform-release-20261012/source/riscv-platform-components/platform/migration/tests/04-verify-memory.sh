#!/usr/bin/env bash
set -euo pipefail
task_results=${TEST_RESULTS:-./migration-test-results}
task_namespace=${TEST_NAMESPACE:-migration-test}
task_migration=${MIGRATION_NAME:-counter-move}
task_pod=$(kubectl get nmg "$task_migration" -n "$task_namespace" -o jsonpath='{.status.targetPod}')
[[ -n "$task_pod" ]]
kubectl get nmg "$task_migration" -n "$task_namespace" -o json > "$task_results/migration.json"
kubectl logs -n "$task_namespace" "$task_pod" --tail=5 > "$task_results/after.jsonl"
python3 - "$task_results/before.json" "$task_results/after.jsonl" "$task_results/migration.json" "$task_results/memory-report.json" <<'PY'
import json, pathlib, sys
before = json.load(open(sys.argv[1]))
after = [json.loads(s) for s in open(sys.argv[2]) if s.strip()]
migration = json.load(open(sys.argv[3])); status = migration['status']
assert status['phase'] == 'Succeeded', '恢复事务尚未成功'
assert status['sourceUID'] != status['targetUID'], '未创建不同 UID 的新 Pod'
mode = migration['spec'].get('mode', 'CrossNode')
assert (status['sourceNode'] == status['targetNode']) == (mode == 'SameNode'), '节点与请求模式不匹配'
if mode == 'SameNode': assert status.get('transferSkipped') is True, '同节点流程未复用本机归档'
assert after, '目标容器没有输出'
assert all(s['boot_id'] == before['boot_id'] for s in after), '内存中的启动标识变化，不能认定状态连续'
assert after[-1]['counter'] > before['counter'], '计数未从原进度继续'
assert all(a['counter'] < b['counter'] for a, b in zip(after, after[1:])), '恢复后计数不单调递增'
report = {'passed': True, 'mode': mode, 'sourceNode': status['sourceNode'], 'targetNode': status['targetNode'],
          'sourceUID': status['sourceUID'], 'targetUID': status['targetUID'], 'bootID': before['boot_id'],
          'counterBefore': before['counter'], 'counterAfter': after[-1]['counter'],
          'observedGapSeconds': (after[0]['time_ns'] - before['time_ns']) / 1e9}
pathlib.Path(sys.argv[4]).write_text(json.dumps(report, indent=2) + '\n')
print('通过：内存启动标识保持，计数继续增长。')
print(json.dumps(report, indent=2))
print('observedGapSeconds 含日志采样间隔，不等同于精确暂停时间。')
PY
