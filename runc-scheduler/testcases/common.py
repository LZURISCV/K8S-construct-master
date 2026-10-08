"""Shared API, evidence collection and cleanup; no test suite entry point."""
import argparse
import copy
import datetime
import json
import os
import pathlib
import ssl
import time
import urllib.error
import urllib.request
import uuid

TERMINAL = {"Succeeded", "Failed", "Removed"}

class API:
    def __init__(self, url, token_file, ca=None):
        self.url = url.rstrip("/")
        self.token = pathlib.Path(token_file).read_text().strip()
        self.context = ssl.create_default_context(cafile=ca) if ca else None

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30, context=self.context) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError("HTTP %d: %s" % (error.code, error.read().decode())) from error

    def state(self): return self.call("GET", "/v1/state")
    def apply(self, workload): return self.call("POST", "/v1/workloads", workload)
    def delete(self, name): return self.call("DELETE", "/v1/workloads/" + name)
    def patch(self, node, patch): return self.call("PATCH", "/v1/nodes/" + node, patch)

    def wait(self, predicate, timeout=180, description="condition"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = predicate(self.state())
            if result: return result
            time.sleep(2)
        raise TimeoutError("Timed out waiting for " + description)

    def exec(self, pod, command):
        op = self.call("POST", "/v1/exec", {"podId": pod, "command": command})["id"]
        result = self.wait(lambda s: s["operations"].get(op) if s["operations"].get(op, {}).get("status") == "Done" else None,
                           timeout=100, description="exec " + op)
        require(result["exitCode"] == 0, "exec failed: " + result.get("output", ""))
        return result.get("output", "")

def require(condition, message):
    if not condition: raise RuntimeError(message)

def pods(state, workload, phase=None):
    generation = state["workloads"].get(workload, {}).get("generation")
    return [p for p in state["pods"].values() if p["workload"] == workload and p["generation"] == generation
            and (p["phase"] == phase if phase else p["phase"] not in TERMINAL)]

def template(nodes, image="demo"):
    return {"image": image, "command": ["/usr/local/bin/rcs", "stress", "-idle-mib", "4"],
            "requests": {"cpuMillis": 100, "memoryBytes": 64 * 1024**2},
            "limits": {"cpuMillis": 300, "memoryBytes": 256 * 1024**2},
            "nodeAffinity": [{"matchExpressions": [{"key": "rcs.node", "operator": "In", "values": nodes}]}]}

def parser(case_id, title):
    p = argparse.ArgumentParser(description=case_id + " " + title + "; executes only this case")
    p.add_argument("--url", default=os.environ.get("RCS_URL", "http://127.0.0.1:8080"))
    p.add_argument("--token-file", default=os.environ.get("RCS_TOKEN_FILE"), required=not os.environ.get("RCS_TOKEN_FILE"))
    p.add_argument("--ca", default=os.environ.get("RCS_CA_FILE"))
    p.add_argument("--nodes", help="comma-separated idle deployment node IDs; defaults to available nodes")
    p.add_argument("--timeout", type=int, default=360, help="seconds per scheduling wait")
    p.add_argument("--report", default="test-results/" + case_id + ".json")
    return p

class Case:
    def __init__(self, api, args, image):
        self.api, self.args, self.image = api, args, image
        self.prefix = "t" + uuid.uuid4().hex[:12]
        self.names, self.changed_taints = [], set()
        self.initial = api.state()
        self.nodes = args.nodes.split(",") if args.nodes else sorted(n for n, v in self.initial["nodes"].items()
            if v["ready"] and not v["cordoned"] and image in v["images"])
        require(bool(self.nodes), "no available " + image + " deployment nodes")
        require(len(set(self.nodes)) == len(self.nodes), "duplicate node IDs")
        for node in self.nodes:
            n = self.initial["nodes"].get(node)
            require(n and n["ready"] and not n["cordoned"] and image in n["images"], "node unavailable: " + node)
            require(not n.get("taints"), "select untainted test nodes: " + node)
            require(n["capacity"]["cpuMillis"] >= (750 if image == "mpi" else 500)
                    and n["capacity"]["memoryBytes"] >= 1024**3, "insufficient allocatable resources: " + node)
        require(not any(p.get("nodeId") in self.nodes and p["phase"] not in TERMINAL
                        for p in self.initial["pods"].values()), "selected test nodes must be idle")

    def apply(self, suffix, spec=None, replicas=1, kind="deployment", autoscaler=None):
        name = self.prefix + "-" + suffix
        if name not in self.names: self.names.append(name)
        w = {"name": name, "kind": kind, "replicas": replicas, "template": spec or template(self.nodes)}
        if autoscaler: w["autoscaler"] = autoscaler
        self.api.apply(w)
        return name

    def running(self, name, count=1):
        return self.api.wait(lambda s: pods(s, name, "Running") if len(pods(s, name, "Running")) == count else None,
                             timeout=self.args.timeout, description=name + " running replicas")

    def pending(self, name, count=1, stable_seconds=8):
        self.api.wait(lambda s: len(pods(s, name, "Pending")) == count,
                      timeout=self.args.timeout, description=name + " pending")
        end = time.monotonic() + stable_seconds
        while True:
            result = pods(self.api.state(), name, "Pending")
            require(len(result) == count, "expected replicas did not remain Pending: " + name)
            if time.monotonic() >= end: return result
            time.sleep(2)

    def taint(self, node):
        self.changed_taints.add(node)
        taint = {"key": "rcs.acceptance", "value": self.prefix, "effect": "NoSchedule"}
        self.api.patch(node, {"taints": copy.deepcopy(self.initial["nodes"][node].get("taints", [])) + [taint]})
        return taint

    def cleanup(self):
        errors = []
        for name in self.names:
            try: self.api.delete(name)
            except Exception as error: errors.append(str(error))
        if self.names:
            try:
                self.api.wait(lambda s: not any(p["workload"] in self.names and p["phase"] not in TERMINAL
                    for p in s["pods"].values()), timeout=self.args.timeout, description="case container cleanup")
            except Exception as error: errors.append(str(error))
        for node in self.changed_taints:
            try: self.api.patch(node, {"taints": self.initial["nodes"][node].get("taints", [])})
            except Exception as error: errors.append(str(error))
        require(not errors, "; ".join(errors))

def run(case_id, args, test, image="demo"):
    report = {"case": case_id, "started": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "runtime": "connected agents / real runc", "status": "FAIL"}
    case = api = None
    try:
        require(args.timeout > 0, "timeout must be positive")
        api = API(args.url, args.token_file, args.ca)
        case = Case(api, args, image)
        report.update(nodes=case.nodes, physicalDeploymentNodes=len(case.nodes),
                      nodeCgroups={n: case.initial["nodes"][n].get("cgroupMode", "unknown") for n in case.nodes})
        report["evidence"] = test(case)
        report["status"] = report["evidence"].pop("status", "PASS")
    except Exception as error:
        report["error"] = str(error)
    finally:
        if api:
            try: report["stateBeforeCleanup"] = api.state()
            except Exception as error: report["stateCaptureError"] = str(error)
        if case:
            try: case.cleanup()
            except Exception as error: report.update(status="FAIL", cleanupError=str(error))
        report["finished"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        target = pathlib.Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(report["status"], case_id, report.get("error", ""))
        print("Report:", target)
    return 0 if report["status"] == "PASS" else (2 if report["status"] in {"PARTIAL", "SKIP"} else 1)

def hpa_spec(node):
    t = template([node])
    t["command"] = ["/usr/local/bin/rcs", "stress", "-idle-mib", "4", "-busy-mib", "160"]
    return t

def autoscaler():
    return {"metric": "memory", "targetPercent": 60, "minReplicas": 1,
            "maxReplicas": 3, "scaleDownWindowSeconds": 20, "cooldownSeconds": 3}

def high_metrics(state, name, count):
    running = pods(state, name, "Running")
    return running if len(running) == count and all(p.get("metricsValid") and
        p.get("memoryUsageBytes", 0) > 64 * 1024**2 * .6 for p in running) else None
