#!/usr/bin/env bash
set -euo pipefail
: "${SOURCE_NODE:?设置 SOURCE_NODE}"
: "${COUNTER_IMAGE:?设置自己构建并上传的 RISC-V COUNTER_IMAGE}"
export TEST_NAMESPACE=${TEST_NAMESPACE:-migration-test}
kubectl create namespace "$TEST_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
python3 - <<'PY' | kubectl create -f -
import json, os
print(json.dumps({'apiVersion':'v1','kind':'Pod',
 'metadata':{'name':'memory-counter','namespace':os.environ['TEST_NAMESPACE'],'annotations':{'migration.riscv.io/enabled':'true'}},
 'spec':{'nodeName':os.environ['SOURCE_NODE'],'restartPolicy':'Never','automountServiceAccountToken':False,
 'containers':[{'name':'counter','image':os.environ['COUNTER_IMAGE'],'resources':{'requests':{'cpu':'100m','memory':'64Mi'},'limits':{'cpu':'1','memory':'128Mi'}}}]}}))
PY
kubectl wait -n "$TEST_NAMESPACE" --for=condition=Ready pod/memory-counter --timeout=180s
kubectl logs -n "$TEST_NAMESPACE" memory-counter --tail=3
