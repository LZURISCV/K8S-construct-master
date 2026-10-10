#!/usr/bin/env python3
"""Change only endpoint flags in the existing systemd EnvironmentFile."""
import os
import pathlib
import re
import sys
import tempfile


def configure(text):
    pattern = r'^KUBELET_ARGS="([^"\n]*)"$'
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) != 1:
        raise ValueError('KUBELET_ARGS 格式不匹配，未修改配置')
    match = matches[0]
    args = re.sub(r'--(?:container-runtime|image-service)-endpoint=[^\s"]+', '', match[1]).strip()
    args += ' --container-runtime-endpoint=unix:///run/rv-migration/cri.sock --image-service-endpoint=unix:///run/rv-migration/cri.sock'
    return text[:match.start()] + 'KUBELET_ARGS="' + args + '"' + text[match.end():]


if __name__ == '__main__':
    path = pathlib.Path(sys.argv[1])
    data = configure(path.read_text())
    fd, temporary = tempfile.mkstemp(prefix='.rv-kubelet-', dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd, 'w') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
