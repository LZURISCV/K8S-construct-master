#!/usr/bin/env bash
set -euo pipefail
: "${SOURCE_NODE:?设置 SOURCE_NODE}"
: "${COUNTER_IMAGE:?设置含 python3 的 RISC-V COUNTER_IMAGE，可使用已有本地镜像}"
export TEST_NAMESPACE=${TEST_NAMESPACE:-migration-test}
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export COUNTER_SCRIPT="$task_dir/examples/counter.py"
kubectl create namespace "$TEST_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
python3 - <<'PY' | kubectl create -f -
import json, os, pathlib
print(json.dumps({'apiVersion':'v1','kind':'Pod',
 'metadata':{'name':'memory-counter','namespace':os.environ['TEST_NAMESPACE'],'annotations':{'migration.riscv.io/enabled':'true'}},
 'spec':{'nodeName':os.environ['SOURCE_NODE'],'restartPolicy':'Never','automountServiceAccountToken':False,
 'containers':[{'name':'counter','image':os.environ['COUNTER_IMAGE'],
 'imagePullPolicy':os.environ.get('COUNTER_PULL_POLICY','IfNotPresent'),
 'command':['python3','-u','-c',pathlib.Path(os.environ['COUNTER_SCRIPT']).read_text()],
 'resources':{'requests':{'cpu':'100m','memory':'64Mi'},'limits':{'cpu':'1','memory':'128Mi'}}}]}}))
PY
kubectl wait -n "$TEST_NAMESPACE" --for=condition=Ready pod/memory-counter --timeout=180s
kubectl logs -n "$TEST_NAMESPACE" memory-counter --tail=3
