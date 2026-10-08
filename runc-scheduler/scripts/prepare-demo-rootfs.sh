#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then echo "Usage: sudo $0 RCS_LINUX_BINARY ROOTFS_DIRECTORY" >&2; exit 1; fi
[[ $EUID -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
binary=$(realpath "$1")
rootfs=$(realpath -m "$2")
[[ "$rootfs" != / && -f "$binary" ]] || { echo 'Invalid binary/rootfs path' >&2; exit 1; }
if [[ -e "$rootfs/usr/local/bin/rcs" ]]; then echo 'Rootfs already prepared; choose a new directory' >&2; exit 1; fi
install -d -m 0755 "$rootfs" "$rootfs/usr/local/bin" "$rootfs/etc" "$rootfs/proc" "$rootfs/sys" "$rootfs/dev" "$rootfs/run" "$rootfs/root"
install -d -m 1777 "$rootfs/tmp"
install -m 0755 "$binary" "$rootfs/usr/local/bin/rcs"
printf 'root:x:0:0:root:/root:/bin/false\n' > "$rootfs/etc/passwd"
printf 'root:x:0:\n' > "$rootfs/etc/group"
echo "Prepared minimal demo rootfs: $rootfs"
echo 'No distribution download is needed for the scheduler/HPA tests.'
