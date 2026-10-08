#!/usr/bin/env python3
"""Create a source/binary delivery zip with checksums and Unix executable modes."""
import argparse
import hashlib
import pathlib
import zipfile

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="../runc-scheduler-v0.1.2.zip")
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]
    output = pathlib.Path(args.output).resolve()
    def included(path):
        relative = path.relative_to(root)
        if any(p in {".git", "__pycache__", "data"} for p in relative.parts): return False
        if path.suffix in {".pyc", ".key", ".token", ".zip"}: return False
        if relative.parts[0] == "test-results" and path.name != "go-test.jsonl": return False
        return path.name != "MANIFEST.sha256"
    files = sorted(p for p in root.rglob("*") if p.is_file() and included(p))
    manifest = root / "MANIFEST.sha256"
    manifest.write_text("\n".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.relative_to(root).as_posix() for p in files) + "\n", encoding="ascii")
    files.append(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            relative = path.relative_to(root)
            info = zipfile.ZipInfo(root.name + "/" + relative.as_posix())
            info.create_system = 3
            executable = path.suffix == ".sh" or path.name.startswith("rcs-linux-")
            info.external_attr = (0o100755 if executable else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad: raise RuntimeError("zip validation failed: " + bad)
    print("Created", output, "files=", len(files), "bytes=", output.stat().st_size)
    print("SHA256", hashlib.sha256(output.read_bytes()).hexdigest())

if __name__ == "__main__": main()
