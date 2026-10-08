#!/usr/bin/env python3
"""Executable/config/CLI smoke test with explicitly simulated node heartbeats."""
import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "testcases"))
from common import API

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    binary = str(pathlib.Path(args.binary).resolve())
    with tempfile.TemporaryDirectory(prefix="rcs-control-smoke-") as temporary:
        root = pathlib.Path(temporary)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        admin = root / "admin.token"; node_token = root / "node.token"
        admin.write_text("a" * 64); node_token.write_text("b" * 64)
        config = root / "control.json"
        config.write_text(json.dumps({"listen": "127.0.0.1:" + str(port), "stateFile": str(root / "state.json"),
             "adminTokenFile": str(admin), "nodeTokenFile": str(node_token), "nodeTimeoutSeconds": 30}))
        url = "http://127.0.0.1:" + str(port)
        api = API(url, str(admin)); agent = API(url, str(node_token))
        def start():
            log = open(root / "controller.log", "ab")
            process = subprocess.Popen([binary, "control", "-config", str(config)], stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            for _ in range(50):
                try: api.state(); return process, log
                except Exception:
                    if process.poll() is not None: raise RuntimeError("control failed: " + (root / "controller.log").read_text())
                    time.sleep(.1)
            process.terminate(); process.wait(timeout=10); log.close()
            raise TimeoutError("control did not start")
        process, log = start()
        try:
            for i in range(3):
                node = {"id": "worker" + str(i + 1), "address": "192.0.2." + str(10 + i), "architecture": "riscv64",
                        "cgroupMode": "v1" if i < 2 else "v2",
                        "capacity": {"cpuMillis": 1000, "memoryBytes": 1024**3}, "images": ["demo"], "labels": {}}
                agent.call("POST", "/v1/heartbeat", {"node": node, "pods": []})
            assert {n["cgroupMode"] for n in api.state()["nodes"].values()} == {"v1", "v2"}
            w = {"name": "smoke", "kind": "deployment", "replicas": 3, "template": {
                "image": "demo", "command": ["/usr/local/bin/rcs", "stress"],
                "requests": {"cpuMillis": 800, "memoryBytes": 64 * 1024**2},
                "limits": {"cpuMillis": 800, "memoryBytes": 128 * 1024**2}}}
            file = root / "workload.json"; file.write_text(json.dumps(w))
            def ctl(*arguments):
                return subprocess.check_output([binary, "ctl", "-url", url, "-token-file", str(admin), *arguments], text=True)
            ctl("apply", str(file)); state = api.state()
            assert {p["nodeId"] for p in state["pods"].values()} == {"worker1", "worker2", "worker3"}
            ctl("label", "worker1", "zone=test")
            assert api.state()["nodes"]["worker1"]["labels"]["zone"] == "test"
            process.terminate(); process.wait(timeout=10); log.close()
            process, log = start()
            restored = json.loads(ctl("get", "pods"))
            assert json.loads(ctl("get", "nodes"))["worker1"]["cgroupMode"] == "v1"
            assert len(restored) == 3 and all(p["phase"] == "Assigned" for p in restored.values())
            ctl("delete", "smoke")
            assert all(p["phase"] == "Terminating" for p in api.state()["pods"].values())
            print("PASS executable/config/CLI, three simulated nodes, resource reservations, restart persistence")
            print("Not a Linux/runc or physical RISC-V acceptance run.")
        finally:
            if process.poll() is None: process.terminate(); process.wait(timeout=10)
            log.close()

if __name__ == "__main__": main()
