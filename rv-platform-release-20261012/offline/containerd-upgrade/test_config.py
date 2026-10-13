import copy
import unittest
from check_config import normalize_registry, validate


def fixtures():
    runtime = {'default_runtime_name': 'runc', 'runtimes': {'runc': {
        'runtime_type': 'io.containerd.runc.v2', 'options': {'SystemdCgroup': True, 'BinaryName': ''}}}}
    cni = {'bin_dir': '/opt/cni/bin', 'conf_dir': '/etc/cni/net.d'}
    common = {'root': '/var/lib/containerd', 'state': '/run/containerd', 'grpc': {'address': '/run/containerd/containerd.sock'}}
    original = dict(copy.deepcopy(common), version=2, plugins={'io.containerd.grpc.v1.cri': {
        'containerd': dict(copy.deepcopy(runtime), snapshotter='overlayfs'), 'cni': cni,
        'sandbox_image': 'hub.oepkgs.net/ruoqing/pause:3.9', 'registry': {'config_path': '/etc/containerd/certs.d'}}})
    migrated = dict(copy.deepcopy(common), version=3, plugins={
        'io.containerd.cri.v1.runtime': {'containerd': runtime, 'cni': {'bin_dirs': ['/opt/cni/bin'], 'conf_dir': '/etc/cni/net.d'}},
        'io.containerd.cri.v1.images': {'snapshotter': 'overlayfs', 'pinned_images': {'sandbox': 'hub.oepkgs.net/ruoqing/pause:3.9'}, 'registry': {'config_path': '/etc/containerd/certs.d'}}})
    return [original, migrated, copy.deepcopy(migrated)]


class ConfigChecks(unittest.TestCase):
    def test_migrated_cni_and_registry_preserved(self):
        self.assertEqual(validate(fixtures())['cniBinDirs'], ['/opt/cni/bin'])

    def test_changed_root_rejected(self):
        configs = fixtures(); configs[1]['root'] = '/new-data'
        with self.assertRaises(ValueError): validate(configs)

    def test_wrong_pause_rejected(self):
        configs = fixtures(); configs[2]['plugins']['io.containerd.cri.v1.images']['pinned_images']['sandbox'] = 'registry.k8s.io/pause:3.10'
        with self.assertRaises(ValueError): validate(configs)

    def test_cgroup_driver_change_rejected(self):
        configs = fixtures(); configs[1]['plugins']['io.containerd.cri.v1.runtime']['containerd']['runtimes']['runc']['options']['SystemdCgroup'] = False
        with self.assertRaises(ValueError): validate(configs)

    def test_registry_change_rejected(self):
        configs = fixtures(); configs[2]['plugins']['io.containerd.cri.v1.images']['registry']['config_path'] = ''
        with self.assertRaises(ValueError): validate(configs)

    def test_imported_config_rejected(self):
        configs = fixtures(); configs[0]['imports'] = ['conf.d/*.toml']
        with self.assertRaises(ValueError): validate(configs)

    def test_omitted_empty_registry_fields_accepted(self):
        configs = fixtures()
        original = configs[0]['plugins']['io.containerd.grpc.v1.cri']['registry']
        original.update(mirrors={}, configs={}, auths={}, headers={})
        validate(configs)

    def test_empty_registry_and_missing_table_accepted(self):
        configs = fixtures()
        configs[0]['plugins']['io.containerd.grpc.v1.cri']['registry'] = {
            'config_path': '', 'mirrors': {}, 'configs': {}, 'auths': {}, 'headers': {}}
        del configs[1]['plugins']['io.containerd.cri.v1.images']['registry']
        configs[2]['plugins']['io.containerd.cri.v1.images']['registry'] = {'config_path': ''}
        validate(configs)

    def test_added_empty_registry_fields_accepted(self):
        configs = fixtures()
        for config in configs[1:]:
            config['plugins']['io.containerd.cri.v1.images']['registry'].update(
                mirrors={}, configs={}, auths={}, headers={})
        validate(configs)

    def test_nonempty_registry_path_added_rejected(self):
        configs = fixtures()
        configs[0]['plugins']['io.containerd.grpc.v1.cri']['registry'] = {'config_path': ''}
        with self.assertRaises(ValueError): validate(configs)

    def test_mirror_endpoint_change_rejected(self):
        configs = fixtures()
        configs[0]['plugins']['io.containerd.grpc.v1.cri']['registry']['mirrors'] = {
            'example.test': {'endpoint': ['https://mirror.example.test']}}
        with self.assertRaises(ValueError): validate(configs)

    def test_empty_host_entry_removed_rejected(self):
        configs = fixtures()
        configs[0]['plugins']['io.containerd.grpc.v1.cri']['registry']['configs'] = {'example.test': {}}
        with self.assertRaises(ValueError): validate(configs)

    def test_header_removed_rejected(self):
        configs = fixtures()
        configs[0]['plugins']['io.containerd.grpc.v1.cri']['registry']['headers'] = {'X-Example': ['value']}
        with self.assertRaises(ValueError): validate(configs)

    def test_changed_credentials_rejected_without_disclosure(self):
        configs = fixtures()
        private = {'example.test': {'auth': {'username': 'test-user', 'password': 'test-secret'}}}
        configs[0]['plugins']['io.containerd.grpc.v1.cri']['registry']['configs'] = copy.deepcopy(private)
        for config in configs[1:]:
            config['plugins']['io.containerd.cri.v1.images']['registry']['configs'] = copy.deepcopy(private)
        configs[2]['plugins']['io.containerd.cri.v1.images']['registry']['configs']['example.test']['auth']['password'] = 'changed-secret'
        with self.assertRaises(ValueError) as caught: validate(configs)
        for private_text in ('test-user', 'test-secret', 'changed-secret', 'example.test'):
            self.assertNotIn(private_text, str(caught.exception))
        self.assertIn('Registry settings changed', str(caught.exception))

    def test_unknown_empty_field_not_ignored(self):
        configs = fixtures()
        configs[1]['plugins']['io.containerd.cri.v1.images']['registry']['unknown_option'] = {}
        with self.assertRaises(ValueError): validate(configs)

    def test_wrong_type_not_ignored(self):
        configs = fixtures()
        configs[1]['plugins']['io.containerd.cri.v1.images']['registry']['mirrors'] = []
        with self.assertRaises(ValueError): validate(configs)

    def test_normalization_does_not_mutate_input(self):
        original = {'config_path': '', 'mirrors': {}, 'configs': {'example.test': {}}}
        before = copy.deepcopy(original)
        self.assertEqual(normalize_registry(original), {'configs': {'example.test': {}}})
        self.assertEqual(original, before)


if __name__ == '__main__':
    unittest.main()
