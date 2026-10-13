#!/usr/bin/env python3
"""Apply the narrow cache-first CRI restore change to pinned containerd v2.1.5."""
from pathlib import Path
import hashlib
import sys

source = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
version = (source / 'version/version.go').read_text()
if '2.1.5' not in version:
    raise SystemExit('Expected containerd v2.1.5 source.')
target = source / 'internal/cri/server/container_checkpoint_linux.go'
text = target.read_text()
if hashlib.sha256(target.read_bytes()).hexdigest() != '3b0e3f0580eccca7f9f3082b1e82bcf7389950caeeafb3374cbe8c2ab1e04792':
    raise SystemExit('Restore source checksum differs from pinned containerd v2.1.5.')
old = 'containerdImage, err := c.client.Pull(ctx, config.RootfsImageRef)'
new = 'containerdImage, err := rvRestoreBaseImage(ctx, c.client, config.RootfsImageRef)'
if text.count(old) != 1:
    raise SystemExit('Pinned restore source did not match; no change made.')
target.write_text(text.replace(old, new), encoding='utf-8')
for name in ('rv_restore_image_cache.go', 'rv_restore_image_cache_test.go'):
    (target.parent / name).write_bytes((here / name).read_bytes())
print('Applied cache-first CRI restore patch; no service has been changed.')
