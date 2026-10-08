#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 3 ]]; then echo "Usage: sudo $0 EXISTING_DISTRO_ROOTFS RCS_BINARY MPI_PRIVATE_KEY" >&2; exit 1; fi
[[ $EUID -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
base=$(cd "$(dirname "$0")/.." && pwd)
rootfs=$(realpath "$1")
[[ "$rootfs" != / ]] || { echo 'Do not use host / as a rootfs' >&2; exit 1; }
install -d -m 0755 "$rootfs/dev" "$rootfs/proc"
if mountpoint -q "$rootfs/dev" || mountpoint -q "$rootfs/proc"; then echo 'Unmount rootfs dev/proc before preparation.' >&2; exit 1; fi
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
chroot "$rootfs" /bin/sh -c 'command -v /usr/sbin/sshd >/dev/null && command -v /usr/bin/ssh >/dev/null' || {
  echo 'Install Open MPI 4.x development packages, OpenSSH server/client and a C compiler inside this distribution rootfs first.' >&2; exit 1;
}
mpi_prefix=''
for candidate in /opt/openmpi /usr/lib64/openmpi /usr/lib/openmpi /usr; do
  if [[ -x "$rootfs$candidate/bin/mpicc" && -x "$rootfs$candidate/bin/mpirun" ]]; then mpi_prefix=$candidate; break; fi
done
[[ -n $mpi_prefix ]] || { echo 'Cannot locate mpicc and mpirun in the rootfs.' >&2; exit 1; }
mpi_path="$mpi_prefix/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mpi_lib="$mpi_prefix/lib:$mpi_prefix/lib64"
chroot "$rootfs" /usr/bin/env PATH="$mpi_path" LD_LIBRARY_PATH="$mpi_lib" mpirun --version | grep -q 'Open MPI) 4\.' || { echo 'Open MPI 4.x required' >&2; exit 1; }
install -d -m 0755 "$rootfs/opt/rcs" "$rootfs/usr/local/bin" "$rootfs/run/sshd" "$rootfs/etc/ssh" "$rootfs/proc" "$rootfs/sys" "$rootfs/dev"
install -d -m 1777 "$rootfs/tmp"
install -d -m 0700 "$rootfs/root/.ssh"
install -m 0755 "$2" "$rootfs/usr/local/bin/rcs"
install -m 0600 "$3" "$rootfs/root/.ssh/id_ed25519"
ssh-keygen -y -f "$3" > "$rootfs/root/.ssh/authorized_keys"
chmod 0600 "$rootfs/root/.ssh/authorized_keys"
# A locked distro root account can reject public-key login when UsePAM=no.
# Set an invalid, non-lock-marker hash inside this test rootfs; passwords remain disabled.
if [[ -f "$rootfs/etc/shadow" ]]; then
  awk -F: 'BEGIN {OFS=":"} $1=="root" {$2="rcs-public-key-only"} {print}' "$rootfs/etc/shadow" > "$rootfs/etc/shadow.rcs"
  chmod 0600 "$rootfs/etc/shadow.rcs"
  mv "$rootfs/etc/shadow.rcs" "$rootfs/etc/shadow"
fi
cat > "$rootfs/root/.ssh/config" <<'EOF'
Host *
  Port 2222
  BatchMode yes
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
EOF
chmod 0600 "$rootfs/root/.ssh/config"
cat > "$rootfs/opt/rcs/mpi-env.sh" <<EOF
export RCS_MPI_PREFIX='$mpi_prefix'
export PATH='$mpi_path'
export LD_LIBRARY_PATH='$mpi_lib'
EOF
cat > "$rootfs/opt/rcs/mpi-info.sh" <<'EOF'
#!/bin/bash
source /opt/rcs/mpi-env.sh
exec ompi_info --version
EOF
chmod 0755 "$rootfs/opt/rcs/mpi-info.sh"
for program in broadcast reduce allgather overlap; do
  install -m 0644 "$base/mpi/$program.c" "$rootfs/opt/rcs/$program.c"
  chroot "$rootfs" /usr/bin/env PATH="$mpi_path" LD_LIBRARY_PATH="$mpi_lib" \
    mpicc -O2 -Wall -Wextra "/opt/rcs/$program.c" -lm -o "/opt/rcs/mpi-$program"
done
install -m 0755 "$base/mpi/launch.sh" "$rootfs/opt/rcs/mpi-launch.sh"
chroot "$rootfs" /usr/bin/ssh-keygen -A
cat > "$rootfs/etc/ssh/sshd_config" <<'EOF'
Port 2222
ListenAddress 0.0.0.0
HostKey /etc/ssh/ssh_host_ed25519_key
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM no
StrictModes yes
PidFile /run/sshd.pid
AuthorizedKeysFile .ssh/authorized_keys
Subsystem sftp internal-sftp
EOF
echo "Prepared MPI rootfs: $rootfs. Use it only on nodes with this architecture and Open MPI version."
