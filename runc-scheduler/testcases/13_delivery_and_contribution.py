#!/usr/bin/env python3
"""Check delivered checksums and compute local contribution evidence separately."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

def main():
    p = argparse.ArgumentParser(description="3-2-0013 delivery and public contribution evidence")
    p.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    p.add_argument("--repo", help="Git repository containing the public contribution commits")
    p.add_argument("--evidence", help="JSON array of actual public commit URLs")
    p.add_argument("--report", default="test-results/3-2-0013.json")
    a = p.parse_args()
    root = pathlib.Path(a.root).resolve()
    target = pathlib.Path(a.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {"case": "3-2-0013", "status": "FAIL", "delivery": {}, "contribution": {"status": "SKIP"}}
    try:
        entries = (root / "MANIFEST.sha256").read_text(encoding="ascii").splitlines()
        failures = []
        for line in entries:
            expected, name = line.split("  ", 1)
            file = (root / name).resolve()
            if root not in file.parents or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                failures.append(name)
        required = ["bin/rcs-linux-riscv64", "docs/openeuler-riscv64.md", "docs/testcases.md"]
        failures += [n for n in required if not (root / n).is_file()]
        report["delivery"] = {"status": "FAIL" if failures else "PASS", "checkedFiles": len(entries), "failures": failures}
        report["status"] = "FAIL" if failures else "PARTIAL"
        if a.repo and a.evidence:
            local = target.with_name(target.stem + "-attribution.json")
            subprocess.run([sys.executable, str(root / "scripts/contribution-report.py"), "--repo", a.repo,
                            "--evidence", a.evidence, "--output", str(local)], check=True)
            report["contribution"] = json.loads(local.read_text(encoding="utf-8"))
            report["localRatioAtLeast50Percent"] = report["contribution"]["ratioPercent"] >= 50
            if not report["localRatioAtLeast50Percent"]: report["status"] = "FAIL"
        elif a.repo or a.evidence:
            raise RuntimeError("--repo and --evidence must be supplied together")
        report["note"] = "Public submission accessibility, community status and agreed counting scope require human verification. Local code attribution cannot certify public contribution."
    except Exception as error: report.update(status="FAIL", error=str(error))
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report["status"], "3-2-0013", "Report:", target)
    return 1 if report["status"] == "FAIL" else 2

if __name__ == "__main__": raise SystemExit(main())
