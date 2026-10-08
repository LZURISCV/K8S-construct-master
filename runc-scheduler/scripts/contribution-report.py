#!/usr/bin/env python3
"""Count current target code attributed to commits listed with public evidence links."""
import argparse
import json
import pathlib
import re
import subprocess

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--evidence", required=True, help="JSON array of {commit, url}")
    parser.add_argument("--paths", nargs="+", default=["types.go", "scheduler.go", "engine.go", "api.go", "agent.go", "main.go", "oci.go", "stats.go", "cgroup.go", "runtime_linux.go", "runtime_other.go"])
    parser.add_argument("--output", default="test-results/contribution.json")
    args = parser.parse_args()
    def git(*arguments):
        return subprocess.check_output(["git", "-C", args.repo, *arguments], text=True, encoding="utf-8")
    evidence = json.loads(pathlib.Path(args.evidence).read_text(encoding="utf-8"))
    included = set()
    links = []
    for entry in evidence:
        commit = entry["commit"]
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit) or not entry["url"].startswith("https://"):
            parser.error("evidence requires commit hash and public HTTPS URL")
        full = git("rev-parse", "--verify", commit + "^{commit}").strip()
        included.add(full); links.append({"commit": full, "url": entry["url"]})
    files = []; total = contributed = 0
    for path in args.paths:
        blame = git("blame", "--line-porcelain", "HEAD", "--", path)
        current = None; lines = credited = 0
        for line in blame.splitlines():
            if re.match(r"^[0-9a-f]{40} \d+ \d+", line): current = line.split()[0]
            elif line.startswith("\t") and line[1:].strip():
                lines += 1
                if current in included: credited += 1
        total += lines; contributed += credited
        files.append({"path": path, "nonemptyCurrentLines": lines, "evidenceAttributedLines": credited})
    report = {"head": git("rev-parse", "HEAD").strip(), "scope": args.paths,
              "method": "nonempty current HEAD lines, git blame, evidence-listed commits",
              "targetLines": total, "evidenceAttributedLines": contributed,
              "ratioPercent": 100 * contributed / total if total else 0,
              "files": files, "evidence": links,
              "status": "REQUIRES_PUBLIC_EVIDENCE_VERIFICATION",
              "note": "This script checks local attribution only. Verify public availability, community submission status and agreed scope before claiming the >=50% criterion."}
    output = pathlib.Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
