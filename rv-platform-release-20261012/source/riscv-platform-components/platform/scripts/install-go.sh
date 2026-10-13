#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 && $# -eq 1 ]] || { echo '可选工具，已有 Go 1.21 不必执行。root: install-go.sh go1.21.0'; exit 1; }
[[ $(uname -m) == riscv64 ]] || { echo '仅用于 RISC-V Linux'; exit 1; }
task_version=$1
task_temp=$(mktemp -d)
trap 'rm -f "$task_temp/go.tar.gz" "$task_temp/dl.json"; rmdir "$task_temp"' EXIT
python3 - "$task_version" "$task_temp" <<'PY'
import hashlib,json,pathlib,sys,urllib.request
version,folder=sys.argv[1:];folder=pathlib.Path(folder)
with urllib.request.urlopen('https://go.dev/dl/?mode=json&include=all',timeout=60) as response:
    releases=json.load(response)
files=[f for r in releases if r['version']==version for f in r['files']
       if f.get('os')=='linux' and f.get('arch')=='riscv64' and f['kind']=='archive']
if len(files)!=1:raise SystemExit('找不到所选版本的官方 linux/riscv64 发行包')
f=files[0];path=folder/'go.tar.gz'
urllib.request.urlretrieve('https://go.dev/dl/'+f['filename'],path)
digest=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
if digest!=f['sha256']:raise SystemExit('Go SHA256 不匹配')
print('已验证',f['filename'],digest)
PY
# Use a versioned installation; do not remove another Go installation.
task_prefix="/opt/$task_version"
[[ ! -e "$task_prefix" ]] || { echo "$task_prefix 已存在，请直接使用该版本"; exit 1; }
mkdir -p "$task_prefix"
tar -xzf "$task_temp/go.tar.gz" --strip-components=1 -C "$task_prefix"
"$task_prefix/bin/go" version
echo "本终端执行: export PATH=$task_prefix/bin:\$PATH"

