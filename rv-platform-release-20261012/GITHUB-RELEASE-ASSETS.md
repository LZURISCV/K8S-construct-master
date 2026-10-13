# GitHub Release asset

The following file is intentionally not stored as a normal Git object because
it is larger than GitHub's 100 MiB per-file limit:

`offline/communication/rv-communication-0.2.0.tar.gz`

Download it from the `rv-platform-release-20261012` release of this repository
and place it at the path above before running the full checksum verification.

Expected SHA-256:

`b2e6b7892877f73db574d97cfeae6e705f3a4da2c8685e8f68e48b20aaeac78d`

Example on Linux:

```bash
base="https://github.com/LZURISCV/K8S-construct-master/releases/download/rv-platform-release-20261012"
mkdir -p offline/communication
curl -fL -o offline/communication/rv-communication-0.2.0.tar.gz \
  "$base/rv-communication-0.2.0.tar.gz"
sha256sum -c SHA256SUMS
```
