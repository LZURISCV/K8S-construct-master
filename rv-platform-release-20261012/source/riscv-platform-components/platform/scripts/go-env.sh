#!/usr/bin/env bash
# Source this file before building project-owned Go modules.
# Respect the installed compiler: never fetch a replacement toolchain.
export GOTOOLCHAIN=local
export GOWORK=off
command -v go >/dev/null || { echo '需要现有 Go 1.21 或更高版本；脚本不会自动安装 Go。' >&2; return 1; }
task_go_version=$(go env GOVERSION)
if [[ ! "$task_go_version" =~ ^go([0-9]+)\.([0-9]+)(\.[0-9]+)?$ ]]; then
  echo "无法识别 Go 版本：$task_go_version" >&2
  return 1
fi
if (( BASH_REMATCH[1] < 1 || (BASH_REMATCH[1] == 1 && BASH_REMATCH[2] < 21) )); then
  echo "需要 Go 1.21 或更高版本，当前为 $task_go_version。" >&2
  return 1
fi
