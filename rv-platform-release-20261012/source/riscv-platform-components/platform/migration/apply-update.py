#!/usr/bin/env python3
"""Copy a verified migration supplement into an existing source directory."""
import hashlib
from pathlib import Path
import shutil
import sys

bundle = Path(__file__).resolve().parent
# The release ZIP places this script in its root, next to platform/migration.
root = Path(sys.argv[1]).expanduser().resolve()
if not (root / 'platform/install.sh').is_file():
    raise SystemExit('请传入现有 riscv-platform-components 源码根目录。')
entries = []
for line in (bundle / 'SHA256SUMS').read_text().splitlines():
    expected, name = line.split('  ', 1)
    source = (bundle / name).resolve()
    if not source.is_relative_to(bundle) or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise SystemExit('补充包校验失败：' + name)
    if name.startswith('platform/migration/'):
        destination = (root / name).resolve()
        if not destination.is_relative_to(root):
            raise SystemExit('源码目录中存在指向外部的路径，停止复制。')
        entries.append((expected, name, source, destination))
manifest = root / 'SHA256SUMS'
existing = {}
if manifest.is_file():
    existing = dict(line.split('  ', 1)[::-1] for line in manifest.read_text().splitlines())
for expected, name, source, destination in entries:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o755 if destination.suffix == '.sh' or destination.name in ('rv-migrate-runtime', 'containerd') else 0o644)
    existing[name] = expected
manifest.write_text(''.join(value + '  ' + name + '\n' for name, value in sorted(existing.items())), encoding='utf-8')
print('迁移源码与预编译程序已更新；未修改任何 systemd 服务或启动测试。')
