#!/usr/bin/env python3
"""Install the bundled CRIU RPMs on openEuler 24.03/riscv64, without repositories."""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys


def run(argv, required=True):
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if required and result.returncode:
        raise RuntimeError("Command failed: " + shlex.join(argv) + "\n" + result.stdout + result.stderr)
    return result


def check_host():
    values = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value.strip('"\'')
    if platform.system() != 'Linux' or platform.machine() != 'riscv64':
        raise RuntimeError('This bundle requires Linux/riscv64.')
    if values.get('ID', '').lower() != 'openeuler' or values.get('VERSION_ID') != '24.03':
        raise RuntimeError('This bundle requires openEuler VERSION_ID=24.03.')
    libc = run(['getconf', 'GNU_LIBC_VERSION']).stdout.strip()
    match = re.fullmatch(r'glibc (\d+)\.(\d+)', libc)
    if not match or tuple(map(int, match.groups())) != (2, 38):
        raise RuntimeError('This bundle was selected for glibc 2.38; found ' + libc)
    for tool in ('rpm', 'dnf'):
        if not shutil.which(tool):
            raise RuntimeError('Required tool missing: ' + tool)
    return {'os': values.get('PRETTY_NAME'), 'architecture': platform.machine(), 'libc': libc}


def verify_bundle(root):
    manifest = json.loads((root / 'manifest.json').read_text())
    packages = manifest['packages']
    names, files = set(), set()
    for package in packages:
        name, filename = package['name'], package['file']
        if name in names or filename in files or Path(filename).name != filename or not filename.endswith('.rpm'):
            raise RuntimeError('Invalid/duplicate RPM entry: ' + filename)
        names.add(name)
        files.add(filename)
        path = root / 'rpms' / filename
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('Missing regular RPM file: ' + str(path))
        if hashlib.sha256(path.read_bytes()).hexdigest() != package['sha256']:
            raise RuntimeError('SHA256 mismatch: ' + filename)
        query = run(['rpm', '-qp', '--qf', '%{NAME}\t%{VERSION}\t%{RELEASE}\t%{ARCH}', str(path)]).stdout
        expected = '\t'.join([name, package['version']['ver'], package['version']['rel'], package['arch']])
        if query != expected or package['arch'] not in ('riscv64', 'noarch'):
            raise RuntimeError('RPM identity/architecture mismatch: ' + filename)
    return manifest


def install_plan(root, manifest):
    by_name = {p['name']: p for p in manifest['packages']}
    if 'criu' not in by_name:
        raise RuntimeError('CRIU package missing from manifest.')
    installed = run(['rpm', '-q', '--qf', '%{VERSION}', 'criu'], required=False)
    if not installed.returncode:
        version = tuple(int(x) for x in re.findall(r'\d+', installed.stdout)[:2])
        if version < (4, 1):
            raise RuntimeError('An older CRIU is installed; inspect it before replacing it: ' + installed.stdout)
        return []
    providers = collections.defaultdict(list)
    for package in manifest['packages']:
        for provided in package['provides']:
            providers[provided['name']].append(package)
        for filename in package.get('files', []):
            providers[filename].append(package)
    selected = {'criu': by_name['criu']}
    queue = collections.deque([by_name['criu']])
    cache = {}
    while queue:
        package = queue.popleft()
        for requirement in package['requires']:
            capability = requirement['name']
            if capability.startswith('rpmlib('):
                continue
            if any(any(p['name'] == capability for p in x['provides']) or capability in x.get('files', [])
                   for x in selected.values()):
                continue
            if capability not in cache:
                cache[capability] = run(['rpm', '-q', '--whatprovides', capability], required=False).returncode == 0
            if cache[capability]:
                continue
            candidates = providers.get(capability, [])
            if not candidates:
                raise RuntimeError('Dependency unavailable in installed system and bundle: ' + capability)
            dependency = min(candidates, key=lambda x: (x['name'] != capability, len(x['name']), x['name']))
            name = dependency['name']
            if run(['rpm', '-q', name], required=False).returncode == 0:
                raise RuntimeError('Installed package does not provide the required capability; inspect it first: '
                                   + name + ' -> ' + capability)
            if name not in selected:
                selected[name] = dependency
                queue.append(dependency)
    paths = [str(root / 'rpms' / p['file']) for p in sorted(selected.values(), key=lambda x: x['name'])]
    # RPM validates versioned requirements and file conflicts before any transaction.
    run(['rpm', '-Uvh', '--test'] + paths)
    return paths


def execute(root, install=False):
    host = check_host()
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo for the RPM transaction precheck.')
    manifest = verify_bundle(root)
    paths = install_plan(root, manifest)
    print(json.dumps({'host': host, 'bundlePackages': len(manifest['packages']),
                      'newPackages': [Path(p).name for p in paths],
                      'mode': 'install' if install else 'plan'}, indent=2), flush=True)
    if not install:
        print('Plan checked. Run with --install to install these local packages.')
        return
    if paths:
        argv = ['dnf', '--disablerepo=*', '--setopt=install_weak_deps=False', '-y', 'install'] + paths
        # No remote repositories, erasure, dependency skipping or package replacement flags.
        subprocess.run(argv, check=True)
    executable = '/usr/sbin/criu'
    version = run([executable, '--version']).stdout.strip()
    print(json.dumps({'criuInstalled': True, 'version': version, 'kernelChecked': False}, indent=2))
    print('Next: sudo /usr/sbin/criu check -v4')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--plan', action='store_true', help='Check the local RPM installation plan; default.')
    group.add_argument('--install', action='store_true', help='Install only required packages that are missing.')
    args = parser.parse_args()
    try:
        execute(Path(__file__).resolve().parent, install=args.install)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
