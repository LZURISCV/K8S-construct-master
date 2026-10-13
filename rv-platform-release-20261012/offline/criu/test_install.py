import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('rv_criu_offline_install', ROOT / 'install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class InstallGates(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'manifest.json').read_text())
        self.missing = {'criu', 'libbsd', 'libnet', 'protobuf-c'}
        self.available = set(self.manifest['systemRequirements'])
        for package in self.manifest['packages']:
            if package['name'] not in self.missing:
                self.available.update(x['name'] for x in package['provides'])
                self.available.update(package.get('files', []))
        self.calls = []

    def rpm(self, argv, required=True):
        self.calls.append(argv)
        code, output = 0, ''
        if argv[:4] == ['rpm', '-q', '--qf', '%{VERSION}']:
            code = 1
        elif argv[:3] == ['rpm', '-q', '--whatprovides']:
            code = int(argv[-1] not in self.available)
        elif argv[:2] == ['rpm', '-q']:
            code = int(argv[-1] in self.missing)
        return subprocess.CompletedProcess(argv, code, output, '')

    def test_plan_adds_only_missing_dependencies(self):
        with patch.object(install, 'run', side_effect=self.rpm):
            paths = install.install_plan(ROOT, self.manifest)
        expected = {p['file'] for p in self.manifest['packages'] if p['name'] in self.missing}
        self.assertEqual({Path(p).name for p in paths}, expected)
        self.assertEqual(self.calls[-1][:3], ['rpm', '-Uvh', '--test'])

    def test_checksum_failure_prevents_package_query(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            self.assertEqual(root.resolve().parent, ROOT)
            (root / 'rpms').mkdir()
            package = self.manifest['packages'][0]
            (root / 'manifest.json').write_text(json.dumps({'packages': [package]}))
            (root / 'rpms' / package['file']).write_bytes(b'corrupted RPM')
            with patch.object(install, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'SHA256 mismatch'):
                    install.verify_bundle(root)
                run.assert_not_called()

    def test_wrong_architecture_prevents_any_command(self):
        with patch.object(install.Path, 'read_text', return_value='ID=openEuler\nVERSION_ID="24.03"\n'), \
             patch.object(install.platform, 'system', return_value='Linux'), \
             patch.object(install.platform, 'machine', return_value='x86_64'), \
             patch.object(install, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Linux/riscv64'):
                install.check_host()
            run.assert_not_called()

    def test_rpm_transaction_failure_prevents_dnf(self):
        def fail_precheck(argv, required=True):
            if argv[:3] == ['rpm', '-Uvh', '--test']:
                raise RuntimeError('simulated missing versioned dependency')
            return self.rpm(argv, required)
        with patch.object(install, 'check_host', return_value={}), \
             patch.object(install.os, 'geteuid', return_value=0, create=True), \
             patch.object(install, 'verify_bundle', return_value=self.manifest), \
             patch.object(install, 'run', side_effect=fail_precheck), \
             patch.object(install.subprocess, 'run') as mutation:
            with self.assertRaisesRegex(RuntimeError, 'versioned dependency'):
                install.execute(ROOT, install=True)
            mutation.assert_not_called()

    def test_existing_criu_is_not_replaced(self):
        with patch.object(install, 'run', return_value=subprocess.CompletedProcess([], 0, '4.2.1', '')) as run:
            self.assertEqual(install.install_plan(ROOT, self.manifest), [])
            self.assertEqual(run.call_count, 1)

    def test_old_installed_provider_is_not_replaced(self):
        def old_provider(argv, required=True):
            if argv == ['rpm', '-q', 'libbsd']:
                return subprocess.CompletedProcess(argv, 0, 'libbsd-old', '')
            return self.rpm(argv, required)
        with patch.object(install, 'run', side_effect=old_provider):
            with self.assertRaisesRegex(RuntimeError, 'Installed package does not provide'):
                install.install_plan(ROOT, self.manifest)
        self.assertFalse(any(x[:3] == ['rpm', '-Uvh', '--test'] for x in self.calls))


if __name__ == '__main__':
    unittest.main()
