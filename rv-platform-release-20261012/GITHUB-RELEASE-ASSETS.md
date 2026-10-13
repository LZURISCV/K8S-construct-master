# Git LFS asset

The following file is stored in this repository with Git LFS because it is
larger than GitHub's 100 MiB normal Git object limit:

`offline/communication/rv-communication-0.2.0.tar.gz`

A normal clone or pull with Git LFS installed downloads the file into the path
above. If the checkout contains only an LFS pointer, run `git lfs pull`.

Expected SHA-256:

`b2e6b7892877f73db574d97cfeae6e705f3a4da2c8685e8f68e48b20aaeac78d`

Verification on Linux:

```bash
git lfs pull
sha256sum -c SHA256SUMS
```
