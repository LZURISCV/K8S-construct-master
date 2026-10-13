#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
task_env=${RV_ENV:-$HOME/rv-platform.env}
if [[ -f "$task_env" ]]; then
  declare -A task_saved=()
  for task_key in TEST_NODE TEST_IMAGE TARGET_NODE TEST_RESULTS MPI_WORKERS TEST_TIMEOUT KEEP_TEST_NAMESPACE MASTER_IP WORKER_IP PROMETHEUS_URL KUBECONFIG; do
    if [[ -v $task_key ]]; then task_saved[$task_key]=${!task_key}; fi
  done
  set -a
  source "$task_env"
  set +a
  for task_key in "${!task_saved[@]}"; do
    printf -v "$task_key" '%s' "${task_saved[$task_key]}"
    export "$task_key"
  done
fi
exec python3 -B "$task_dir/verify.py"
