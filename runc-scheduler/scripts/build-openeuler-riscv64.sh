#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ $(uname -m) == riscv64 ]] || { echo 'Native build requires a riscv64 machine; use build.sh/build.ps1 for cross compilation.' >&2; exit 1; }
source /etc/os-release
[[ ${ID,,} == openeuler ]] || { echo 'This script targets openEuler.' >&2; exit 1; }
command -v go >/dev/null || { echo 'Go >= 1.22 required; see docs/openeuler-riscv64.md.' >&2; exit 1; }
version=$(go env GOVERSION)
[[ $version =~ ^go([0-9]+)\.([0-9]+) ]] || { echo "Unrecognized Go version: $version" >&2; exit 1; }
(( BASH_REMATCH[1] > 1 || (BASH_REMATCH[1] == 1 && BASH_REMATCH[2] >= 22) )) || { echo "Go >= 1.22 required; got $version" >&2; exit 1; }
unset GOOS GOARCH
export CGO_ENABLED=0 GOTOOLCHAIN=local
mkdir -p bin test-results
go test -json ./... | tee test-results/go-test.jsonl
go vet ./...
GOOS=linux GOARCH=riscv64 go build -trimpath -ldflags='-s -w -X main.version=0.1.2' -o bin/rcs-linux-riscv64 .
sha256sum bin/rcs-linux-riscv64 > bin/SHA256SUMS-riscv64
file bin/rcs-linux-riscv64
cat bin/SHA256SUMS-riscv64
