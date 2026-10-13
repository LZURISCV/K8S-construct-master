#!/usr/bin/env bash
# Remove only this project's Grafana objects; keep the shared Helm release.
set -euo pipefail
task_namespace=${1:-rv-monitoring}
kubectl delete \
  deployment/rv-grafana \
  service/rv-grafana \
  configmap/rv-grafana-config \
  secret/rv-grafana-admin \
  --namespace="$task_namespace" --ignore-not-found --wait=true --timeout=120s
echo 'Grafana resources removed. Use the new Prometheus-only package for future upgrades.'
echo 'The previous verify.py checks Grafana; replace it with the new CLI verifier.'
kubectl get deployment/rv-prometheus service/rv-prometheus --namespace="$task_namespace"
