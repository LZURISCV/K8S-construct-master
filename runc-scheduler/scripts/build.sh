#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v go >/dev/null || { echo 'Install Go >= 1.22 first: https://go.dev/dl/' >&2; exit 1; }
mkdir -p bin
export CGO_ENABLED=0
go test ./...
go vet ./...
for arch in amd64 arm64 riscv64; do
  GOOS=linux GOARCH="$arch" go build -trimpath -ldflags='-s -w -X main.version=0.1.2' -o "bin/rcs-linux-$arch" .
done
GOOS=windows GOARCH=amd64 go build -trimpath -ldflags='-s -w -X main.version=0.1.2' -o bin/rcs-windows-amd64.exe .
sha256sum bin/rcs-* > bin/SHA256SUMS
cat bin/SHA256SUMS
