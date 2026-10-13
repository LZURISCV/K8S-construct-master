#!/usr/bin/env bash
# Run on a worker that already has the communication image in k8s.io.
set -euo pipefail
umask 077
task_image=${TEST_IMAGE:-localhost/rv-communication:0.2.0}
task_output=${1:-$HOME/rv-communication-export-$(date +%Y%m%d-%H%M%S)}
if [[ -e "$task_output" ]]; then
  echo "输出目录已存在，为防止混入旧分片，请指定一个新目录：$task_output" >&2
  exit 1
fi
if ! sudo ctr -n k8s.io images ls -q | awk -v image="$task_image" '$0==image{found=1} END{exit !found}'; then
  echo "当前主机没有镜像 $task_image。请在实际运行该镜像的工作端执行。" >&2
  exit 1
fi
mkdir -p "$task_output/git-files"
sudo ctr -n k8s.io images export --platform linux/riscv64 \
  "$task_output/rv-communication-0.2.0.tar" "$task_image"
sudo chown "$(id -u):$(id -g)" "$task_output/rv-communication-0.2.0.tar"
gzip -1 -c "$task_output/rv-communication-0.2.0.tar" \
  > "$task_output/rv-communication-0.2.0.tar.gz"
split -b 90M -d -a 3 "$task_output/rv-communication-0.2.0.tar.gz" \
  "$task_output/git-files/rv-communication-0.2.0.tar.gz.part-"
(
  cd "$task_output"
  sha256sum rv-communication-0.2.0.tar.gz > git-files/ARCHIVE.SHA256
)
(
  cd "$task_output/git-files"
  sha256sum rv-communication-0.2.0.tar.gz.part-* > PARTS.SHA256
  sha256sum -c PARTS.SHA256
)
printf '工作端导出完成：%s\n只把其中 git-files 目录上传 Git。\n' "$task_output"
