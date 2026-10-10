#!/usr/bin/env bash
set -euo pipefail
command -v kubectl >/dev/null
command -v python3 >/dev/null
kubectl get nodes -o wide
kubectl version -o json
kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes
kubectl get crd mpijobs.kubeflow.org resourceclaims.platform.riscv.io hostnetworkworkloads.platform.riscv.io scenariopolicies.platform.riscv.io nodemigrations.migration.riscv.io
echo '基础 API 检查通过。测试镜像应包含 python3、sleep、MPI、sshd、iproute、iperf3。'

