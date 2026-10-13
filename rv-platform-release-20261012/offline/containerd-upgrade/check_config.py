#!/usr/bin/env python3
"""Compare safety-critical containerd settings across config v2/v3 migration."""
import json
from pathlib import Path
import sys

try:
    import tomllib
except ImportError:
    raise SystemExit('STOP: this checker requires Python 3.11 or later (tomllib).')


def summary(config):
    plugins = config['plugins']
    if config['version'] == 2:
        runtime = images = plugins['io.containerd.grpc.v1.cri']
        pause = images['sandbox_image']
        snapshotter = runtime['containerd']['snapshotter']
    elif config['version'] == 3:
        runtime = plugins['io.containerd.cri.v1.runtime']
        images = plugins['io.containerd.cri.v1.images']
        pause = images['pinned_images']['sandbox']
        snapshotter = images['snapshotter']
    else:
        raise ValueError('Only configuration version 2 or 3 is supported.')
    cni = runtime['cni']
    bins = cni.get('bin_dirs') or [cni.get('bin_dir', '')]
    name = runtime['containerd']['default_runtime_name']
    selected = runtime['containerd']['runtimes'][name]
    return {
        'root': config['root'], 'state': config['state'],
        'address': config['grpc']['address'], 'pause': pause,
        'cniBinDirs': bins, 'cniConfDir': cni['conf_dir'],
        'defaultRuntime': name, 'runtimeType': selected['runtime_type'],
        'systemdCgroup': selected['options'].get('SystemdCgroup', False),
        'runcBinary': selected['options'].get('BinaryName', ''),
        'snapshotter': snapshotter,
    }


def normalize_registry(registry):
    """Ignore only omitted top-level zero values; retain every nested setting."""
    if not isinstance(registry, dict):
        raise ValueError('Registry configuration must be a table.')
    normalized = dict(registry)
    if normalized.get('config_path') == '':
        normalized.pop('config_path')
    for key in ('mirrors', 'configs', 'auths', 'headers'):
        if normalized.get(key) == {}:
            normalized.pop(key)
    return normalized


def validate(configs):
    expected = summary(configs[0])
    if configs[0]['version'] != 2:
        raise ValueError('The initial configuration must be version 2.')
    if configs[0].get('imports') or configs[0].get('disabled_plugins') or configs[0].get('required_plugins'):
        raise ValueError('Imported/disabled/required plugins need a separate review.')
    if expected != {
        'root': '/var/lib/containerd', 'state': '/run/containerd',
        'address': '/run/containerd/containerd.sock',
        'pause': 'hub.oepkgs.net/ruoqing/pause:3.9',
        'cniBinDirs': ['/opt/cni/bin'], 'cniConfDir': '/etc/cni/net.d',
        'defaultRuntime': 'runc', 'runtimeType': 'io.containerd.runc.v2',
        'systemdCgroup': True, 'runcBinary': '', 'snapshotter': 'overlayfs',
    }:
        raise ValueError('The initial settings differ from the confirmed k8snode1 environment.')
    original_registry = configs[0]['plugins']['io.containerd.grpc.v1.cri'].get('registry', {})
    for config in configs[1:]:
        if config['version'] != 3:
            raise ValueError('Migrated configuration must be version 3.')
        for key, value in expected.items():
            if summary(config)[key] != value:
                raise ValueError('Migration changed setting: ' + key)
        if config.get('imports') or config.get('disabled_plugins') or config.get('required_plugins'):
            raise ValueError('Migration changed plugin activation/import settings.')
        registry = config['plugins']['io.containerd.cri.v1.images'].get('registry', {})
        if normalize_registry(registry) != normalize_registry(original_registry):
            raise ValueError('Registry settings changed; inspect privately before switching.')
    return expected


def main():
    if len(sys.argv) != 4:
        raise SystemExit('Usage: check_config.py ORIGINAL CONFIG_2_0 CONFIG_2_1')
    try:
        configs = [tomllib.loads(Path(p).read_text()) for p in sys.argv[1:]]
        result = validate(configs)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit('STOP: ' + str(exc))
    print(json.dumps({'configurationChecked': True, 'settings': result}, indent=2))


if __name__ == '__main__':
    main()
