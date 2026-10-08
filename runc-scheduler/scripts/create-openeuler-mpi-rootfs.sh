#!/usr/bin/env bash
# Native RISC-V rootfs, using host-configured openEuler repositories and pinned MPI source.
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 ]]; then echo "Usage: sudo $0 NEW_ROOTFS [RELEASEVER]" >&2; exit 1; fi
[[ $EUID -eq 0 && $(uname -m) == riscv64 ]] || { echo 'Run as root on riscv64 openEuler.' >&2; exit 1; }
source /etc/os-release
[[ ${ID,,} == openeuler ]] || { echo 'openEuler required' >&2; exit 1; }
release=${2:-${VERSION_ID:-}}
[[ -n $release ]] || { echo 'Supply the actual DNF releasever as argument 2.' >&2; exit 1; }
rootfs=$(realpath -m "$1")
[[ "$rootfs" != / && "$rootfs" != /etc && "$rootfs" != /usr && "$rootfs" != /var ]] || { echo 'Invalid rootfs target.' >&2; exit 1; }
if [[ -e "$rootfs" && ( ! -d "$rootfs" || -n $(find "$rootfs" -mindepth 1 -maxdepth 1 -print -quit) ) ]]; then
  echo 'Choose a new or empty rootfs directory.' >&2; exit 1
fi
command -v dnf >/dev/null
command -v curl >/dev/null
command -v sha256sum >/dev/null
install -d -m 0755 "$rootfs"
# Repository URLs and GPG verification remain those configured on this host.
dnf --installroot="$rootfs" --releasever="$release" --setopt=reposdir=/etc/yum.repos.d \
  --setopt=install_weak_deps=False -y install bash coreutils glibc glibc-devel gcc make \
  tar gzip openssh-server openssh-clients util-linux shadow-utils
install -d -m 0755 "$rootfs/etc" "$rootfs/dev" "$rootfs/proc" "$rootfs/sys" "$rootfs/run" "$rootfs/root"
install -d -m 1777 "$rootfs/tmp"
cp -L /etc/resolv.conf "$rootfs/etc/resolv.conf"
archive="$rootfs/tmp/openmpi-4.1.8.tar.gz"
curl --fail --location --proto '=https' --tlsv1.2 \
  https://download.open-mpi.org/release/open-mpi/v4.1/openmpi-4.1.8.tar.gz -o "$archive"
printf '%s  %s\n' fb41086bbed9300baa2f3d7572491facfe5257412fa524ec5a396aa9101d5c62 "$archive" | sha256sum -c -
mounted_dev=0; mounted_proc=0
cleanup() {
  local status=$?
  if [[ $mounted_proc == 1 ]]; then umount "$rootfs/proc" || status=1; fi
  if [[ $mounted_dev == 1 ]]; then umount "$rootfs/dev" || status=1; fi
  exit "$status"
}
trap cleanup EXIT
mount --bind /dev "$rootfs/dev"; mounted_dev=1
mount -t proc proc "$rootfs/proc"; mounted_proc=1
jobs=${RCS_BUILD_JOBS:-2}
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'RCS_BUILD_JOBS must be a positive integer.' >&2; exit 1; }
chroot "$rootfs" /bin/bash -s -- "$jobs" <<'EOF'
set -euo pipefail
cd /tmp
tar -xzf openmpi-4.1.8.tar.gz
cd openmpi-4.1.8
./configure --prefix=/opt/openmpi --disable-mpi-fortran --disable-oshmem \
  --without-verbs --without-ucx --without-libfabric --with-hwloc=internal --with-libevent=internal
make -j "$1"
make install
/opt/openmpi/bin/mpirun --version
EOF
echo "Built Open MPI 4.1.8 in $rootfs. Next run prepare-mpi-rootfs.sh."
echo 'The source/build directory is retained in rootfs/tmp; clean it before container deployment if desired.'
