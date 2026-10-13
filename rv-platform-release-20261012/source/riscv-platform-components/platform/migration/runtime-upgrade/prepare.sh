#!/usr/bin/env bash
# Prepare versioned binaries and configs; never restart a running service.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'STOP: use sudo' >&2; exit 1; }
[[ $(uname -m) == riscv64 ]] || { echo 'STOP: riscv64 required' >&2; exit 1; }
bundle_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_dir=$(realpath -- "${1:?Usage: prepare.sh /home/lzu/riscv-platform-components}")
for tool in python3 sha256sum tar install systemctl ctr flock; do command -v "$tool" >/dev/null; done
python3 -c 'import tomllib'
install -d -m 0700 /var/lib/rv-containerd-upgrade
exec 9>/var/lib/rv-containerd-upgrade/lock
flock -n 9 || { echo 'STOP: another upgrade command is running' >&2; exit 1; }
[[ ! -e /var/lib/rv-containerd-upgrade/prepared.sha256 ]] || { echo 'STOP: already prepared; retain the existing files and continue with switch.sh' >&2; exit 1; }
[[ ! -e /etc/systemd/system/containerd.service.d/30-rv-version.conf ]] || { echo 'STOP: an override already exists; inspect it first' >&2; exit 1; }
[[ $(systemctl show containerd -p ActiveState --value) == active ]]
[[ $(systemctl show containerd -p KillMode --value) == process ]]
[[ $(systemctl show containerd -p Delegate --value) == yes ]]
main_pid=$(systemctl show containerd -p MainPID --value)
[[ $(readlink -f "/proc/$main_pid/exe") == $(readlink -f /usr/bin/containerd) ]] || { echo 'STOP: unexpected running containerd binary' >&2; exit 1; }
ctr --address /run/containerd/containerd.sock version > /var/lib/rv-containerd-upgrade/original-version.txt
python3 - /var/lib/rv-containerd-upgrade/original-version.txt <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
match = re.search(r'(?ms)^Server:\s*\n.*?^\s*Version:\s*v?(\S+)', text)
if not match or not match[1].startswith('1.6.'):
    raise SystemExit('STOP: preparation expects the confirmed containerd 1.6 server.')
PY
(cd "$bundle_dir"; sha256sum -c SHA256SUMS)
printf '%s\n' \
  '87f0efef9c714465b3b51b289e546e70063bedd81114e9eda85868b58876335c  containerd' \
  '08b133d82fb49bc57bc5b530328f2b8f84b677d18ce6e4b88667ad7bdc9be8bf  containerd-shim-runc-v2' \
  'e55c15a4a8b6624fe0c4a256c83002e0b1d4341e1cc3e41f255d286d8b0ec5a4  ctr' \
  > /var/lib/rv-containerd-upgrade/expected-2.1.sha256
(cd "$source_dir/platform/migration/containerd-bin"; sha256sum -c /var/lib/rv-containerd-upgrade/expected-2.1.sha256; sha256sum -c SHA256SUMS)
install -d -m 0755 /opt/rv-platform/containerd/2.0.13/bin /opt/rv-platform/containerd/2.1.5/bin
# Extract only three regular files, never archive-provided links or other paths.
python3 - "$bundle_dir" <<'PY'
import pathlib, shutil, tarfile
import sys
archive = pathlib.Path(sys.argv[1]) / 'containerd-static-2.0.13-linux-riscv64.tar.gz'
target = pathlib.Path('/opt/rv-platform/containerd/2.0.13/bin')
with tarfile.open(archive) as tar:
    for name in ('containerd', 'containerd-shim-runc-v2', 'ctr'):
        member = tar.getmember('bin/' + name)
        if not member.isfile():
            raise SystemExit('STOP: expected a regular binary: ' + name)
        with tar.extractfile(member) as src, (target / name).open('wb') as dst:
            shutil.copyfileobj(src, dst)
        (target / name).chmod(0o755)
PY
install -m 0755 "$source_dir/platform/migration/containerd-bin/"{containerd,containerd-shim-runc-v2,ctr} /opt/rv-platform/containerd/2.1.5/bin/
/opt/rv-platform/containerd/2.0.13/bin/containerd --version
/opt/rv-platform/containerd/2.1.5/bin/containerd --version
umask 077
backup_dir=$(mktemp -d /var/lib/rv-containerd-upgrade/backup.XXXXXXXX)
cp -p /etc/containerd/config.toml "$backup_dir/config.toml"
cp -p /etc/kubernetes/kubelet.conf "$backup_dir/kubelet.conf"
systemctl cat containerd.service > "$backup_dir/containerd.service.txt"
ctr --address /run/containerd/containerd.sock -n k8s.io images ls > "$backup_dir/images.txt"
ctr --address /run/containerd/containerd.sock -n k8s.io tasks ls > "$backup_dir/tasks.txt"
config20=$(mktemp /etc/containerd/.rv-2.0.XXXXXXXX)
config21=$(mktemp /etc/containerd/.rv-2.1.XXXXXXXX)
trap 'rm -f -- "$config20" "$config21"' EXIT
/opt/rv-platform/containerd/2.0.13/bin/containerd --config /etc/containerd/config.toml config migrate > "$config20"
# Migrate the original v2 again so 2.1 can also migrate legacy CNI bin_dir.
/opt/rv-platform/containerd/2.1.5/bin/containerd --config /etc/containerd/config.toml config migrate > "$config21"
python3 "$bundle_dir/check_config.py" /etc/containerd/config.toml "$config20" "$config21"
install -m 0600 "$config20" /etc/containerd/config-rv-2.0.toml
install -m 0600 "$config21" /etc/containerd/config-rv-migration.toml
sha256sum /etc/containerd/config.toml /etc/containerd/config-rv-2.0.toml /etc/containerd/config-rv-migration.toml \
  /opt/rv-platform/containerd/{2.0.13,2.1.5}/bin/{containerd,containerd-shim-runc-v2,ctr} \
  > /var/lib/rv-containerd-upgrade/prepared.sha256
printf 'PREPARED. Configuration/inventory backup: %s\nNo service has been restarted.\n' "$backup_dir"
