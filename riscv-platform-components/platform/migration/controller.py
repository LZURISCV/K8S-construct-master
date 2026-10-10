#!/usr/bin/env python3
"""Restartable NodeMigration reconciler. Python stdlib + kubectl + OpenSSH.

The checkpoint is a memory/process checkpoint, not a replacement from the image.
Source execution stops before transfer. Any failure after that point retains the
archive and source lock and is reported as RecoveryRequired, never as success.
"""
import argparse
import copy
import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from datetime import datetime, timezone

GROUP = "/apis/migration.riscv.io/v1alpha1"
LOCK = "migration.riscv.io/lock"
OPT_IN = "migration.riscv.io/enabled"
RESTORE = "migration.riscv.io/restore"
FINALIZER = "migration.riscv.io/retain-state"
TERMINAL = {"Succeeded", "Failed", "RecoveryRequired"}
SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def now():
    return datetime.now(timezone.utc).isoformat()


def spec_hash(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def quantity(value):
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)", str(value))
    if not match:
        raise ValueError(f"unsupported resource quantity: {value}")
    factors = {"": 1, "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal(".001")}
    factors.update({s: 1000 ** (i + 1) for i, s in enumerate(("k", "M", "G", "T", "P", "E"))})
    factors.update({s: 1024 ** (i + 1) for i, s in enumerate(("Ki", "Mi", "Gi", "Ti", "Pi", "Ei"))})
    if match[2] not in factors:
        raise ValueError(f"unsupported resource suffix: {value}")
    return Decimal(match[1]) * factors[match[2]]


def requested(pod, resource):
    s = pod["spec"]
    # Conservative for init sidecars: sum all containers rather than undercount.
    value = sum((quantity(c.get("resources", {}).get("requests", {}).get(resource, "0"))
                 for c in s.get("containers", []) + s.get("initContainers", [])), Decimal(0))
    return max(value, quantity(s.get("resources", {}).get("requests", {}).get(resource, "0"))) + quantity(s.get("overhead", {}).get(resource, "0"))


class Kube:
    def __init__(self, kubeconfig=None):
        self.base = ["kubectl"] + (["--kubeconfig", kubeconfig] if kubeconfig else [])

    def run(self, *args, data=None, missing=False):
        p = subprocess.run(self.base + list(args), input=None if data is None else json.dumps(data),
                           text=True, capture_output=True, timeout=90)
        if p.returncode:
            if missing and ("NotFound" in p.stderr or "not found" in p.stderr):
                return None
            raise RuntimeError(p.stderr.strip()[:4000])
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def pod(self, ns, name):
        return self.run("get", "pod", name, "-n", ns, "-o", "json", missing=True)

    def status(self, obj, status):
        ns, name = obj["metadata"]["namespace"], obj["metadata"]["name"]
        current = self.run("get", "--raw", f"{GROUP}/namespaces/{ns}/nodemigrations/{name}")
        current["status"] = status
        return self.run("replace", "--raw", f"{GROUP}/namespaces/{ns}/nodemigrations/{name}/status", "-f", "-", data=current)

    def patch_pod(self, pod, changes):
        ops = [{"op": "test", "path": "/metadata/uid", "value": pod["metadata"]["uid"]},
               {"op": "test", "path": "/metadata/resourceVersion", "value": pod["metadata"]["resourceVersion"]}]
        return self.run("patch", "pod", pod["metadata"]["name"], "-n", pod["metadata"]["namespace"],
                        "--type=json", "-p", json.dumps(ops + changes), "-o", "json")

    def finalizer(self, obj, add):
        values = list(obj["metadata"].get("finalizers", []))
        if add and FINALIZER not in values:
            values.append(FINALIZER)
        if not add and FINALIZER in values:
            values.remove(FINALIZER)
        return self.run("patch", "nodemigration", obj["metadata"]["name"], "-n", obj["metadata"]["namespace"],
                        "--type=json", "-p", json.dumps([
                            {"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
                            {"op": "add", "path": "/metadata/finalizers", "value": values}]), "-o", "json")

    def delete_pod(self, ns, name, uid):
        # Raw DeleteOptions carry a server-side UID precondition. A name-reused
        # Pod is never deleted by a stale migration transaction.
        return self.run("delete", "--raw", f"/api/v1/namespaces/{ns}/pods/{name}", "-f", "-",
                        data={"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid}})


class SSH:
    def __init__(self, config):
        self.config = config
        self.timeout = int(config.get("operationTimeoutSeconds", 1200))

    def argv(self, node, *args):
        record = self.config["nodes"][node]
        for key in ("host", "user"):
            if not SAFE.fullmatch(record[key]):
                raise ValueError(f"invalid SSH {key} for {node}")
        helper = self.config.get("nodeHelper", "/usr/local/libexec/rv-migrate-runtime")
        if not helper.startswith("/"):
            raise ValueError("nodeHelper must be absolute")
        return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                "-i", record["identityFile"], "-p", str(int(record.get("port", 22))),
                f'{record["user"]}@{record["host"]}', shlex.join([helper] + list(args))]

    def run(self, node, *args, data=None):
        p = subprocess.run(self.argv(node, *args), input=None if data is None else json.dumps(data),
                           text=True, capture_output=True, timeout=self.timeout)
        if p.returncode:
            raise RuntimeError(f"node {node}: {p.stderr.strip()[:4000]}")
        return json.loads(p.stdout)

    def transfer(self, source, target, manifest):
        # Stream through the control node without keeping checkpoint data there.
        with tempfile.TemporaryFile() as srcerr, tempfile.TemporaryFile() as dsterr:
            src = subprocess.Popen(self.argv(source, "export", manifest["id"]), stdout=subprocess.PIPE, stderr=srcerr)
            try:
                dst = subprocess.Popen(self.argv(target, "import", json.dumps(manifest, separators=(",", ":"))),
                                       stdin=src.stdout, stdout=subprocess.PIPE, stderr=dsterr)
                src.stdout.close()
                try:
                    output, _ = dst.communicate(timeout=self.timeout)
                    source_rc = src.wait(timeout=30)
                    if source_rc or dst.returncode:
                        srcerr.seek(0); dsterr.seek(0)
                        raise RuntimeError((srcerr.read() + dsterr.read()).decode(errors="replace")[:4000])
                    received = json.loads(output)
                    if received["sha256"] != manifest["sha256"]:
                        raise RuntimeError("target manifest checksum mismatch")
                    return received
                finally:
                    if dst.poll() is None:
                        dst.kill(); dst.wait()
            finally:
                if src.poll() is None:
                    src.kill(); src.wait()


def validate_workload(pod):
    if not pod:
        raise ValueError("source Pod does not exist")
    m, s = pod["metadata"], pod["spec"]
    if m.get("deletionTimestamp") or m.get("ownerReferences"):
        raise ValueError("v1 supports standalone Pods only; controller-owned/deleting Pods are rejected")
    if m.get("annotations", {}).get(OPT_IN) != "true":
        raise ValueError(f"source must explicitly opt in with {OPT_IN}=true")
    if s.get("restartPolicy") != "Never" or s.get("automountServiceAccountToken") is not False:
        raise ValueError("source must use restartPolicy: Never and automountServiceAccountToken: false")
    if len(s.get("containers", [])) != 1 or s.get("initContainers") or s.get("ephemeralContainers"):
        raise ValueError("v1 supports exactly one container without init/ephemeral containers")
    if s.get("volumes") or s.get("imagePullSecrets") or s.get("hostNetwork") or s.get("hostPID") or s.get("hostIPC") or s.get("shareProcessNamespace"):
        raise ValueError("v1 rejects external volumes and shared/host namespaces")
    if s.get("affinity") or s.get("schedulingGates") or s.get("runtimeClassName"):
        raise ValueError("v1 rejects affinity, scheduling gates and custom runtime classes")
    c = s["containers"][0]
    if any(c.get(k) for k in ("livenessProbe", "startupProbe", "readinessProbe", "lifecycle", "stdin", "tty", "volumeMounts", "volumeDevices")):
        raise ValueError("v1 rejects probes, lifecycle hooks, interactive IO and external device/volume mounts")
    if c.get("securityContext", {}).get("privileged"):
        raise ValueError("privileged workload migration is not supported")
    resources = c.get("resources", {})
    if any(key not in ("cpu", "memory", "ephemeral-storage") for field in ("requests", "limits") for key in resources.get(field, {})):
        raise ValueError("v1 rejects extended/device resources")
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if pod.get("status", {}).get("phase") != "Running" or len(statuses) != 1 or not statuses[0].get("state", {}).get("running"):
        raise ValueError("source container must be running")
    cid = statuses[0].get("containerID", "")
    if not re.fullmatch(r"containerd://[a-f0-9]{64}", cid):
        raise ValueError("source runtime must be containerd with a full container ID")
    return cid.removeprefix("containerd://")


def target_template(source, migration):
    obj = {"apiVersion": "v1", "kind": "Pod", "metadata": {
        "name": migration["status"]["targetPod"], "namespace": source["metadata"]["namespace"],
        "labels": copy.deepcopy(source["metadata"].get("labels", {})),
        "annotations": {RESTORE: migration["metadata"]["uid"]},
        "ownerReferences": [{"apiVersion": "migration.riscv.io/v1alpha1", "kind": "NodeMigration",
                             "name": migration["metadata"]["name"], "uid": migration["metadata"]["uid"], "controller": True}]},
        "spec": copy.deepcopy(source["spec"])}
    for k in ("nodeName", "hostname", "subdomain"):
        obj["spec"].pop(k, None)
    # Create pending, obtain its UID, authorize that exact UID, then bind to node.
    obj["spec"]["schedulerName"] = "rv-migration-reserved"
    obj["spec"]["containers"][0]["imagePullPolicy"] = "IfNotPresent"
    return obj


class Controller:
    def __init__(self, kube, nodes, config):
        self.kube, self.nodes, self.config = kube, nodes, config

    def update(self, obj, phase, **values):
        status = dict(obj.get("status", {}))
        status.update(values, phase=phase, updatedAt=now())
        return self.kube.status(obj, status)

    def unlock(self, obj):
        s, m = obj.get("status", {}), obj["metadata"]
        pod = self.kube.pod(m["namespace"], s.get("sourcePod", obj["spec"]["sourcePod"]))
        if pod and pod["metadata"]["uid"] == s.get("sourceUID") and pod["metadata"].get("annotations", {}).get(LOCK) == m["uid"]:
            self.kube.patch_pod(pod, [{"op": "remove", "path": "/metadata/annotations/migration.riscv.io~1lock"}])

    def step(self, obj):
        phase = obj.get("status", {}).get("phase", "Pending")
        if phase in TERMINAL:
            if phase in ("Succeeded", "Failed") and FINALIZER in obj["metadata"].get("finalizers", []):
                if phase == "Failed":
                    self.unlock(obj)
                self.kube.finalizer(obj, False)
            return
        try:
            if obj["metadata"].get("deletionTimestamp"):
                if phase in ("Pending", "Validating"):
                    self.unlock(obj); self.kube.finalizer(obj, False)
                else:
                    self.update(obj, "RecoveryRequired", failedPhase=phase, message="deletion requested after checkpoint began; retained finalizer and state; inspect before manual recovery")
                return
            if phase != "Pending" and obj.get("status", {}).get("specHash") != spec_hash(obj["spec"]):
                raise ValueError("migration spec changed during execution")
            getattr(self, "phase_" + phase)(obj)
        except Exception as error:
            if phase in ("Pending", "Validating"):
                self.unlock(obj)
                updated = self.update(obj, "Failed", failedPhase=phase, message=str(error))
                self.kube.finalizer(updated, False)
            else:
                self.update(obj, "RecoveryRequired", failedPhase=phase, message=str(error))

    def phase_Pending(self, obj):
        obj = self.kube.finalizer(obj, True)
        self.update(obj, "Validating", specHash=spec_hash(obj["spec"]), startedAt=now())

    def phase_Validating(self, obj):
        ns, spec, uid = obj["metadata"]["namespace"], obj["spec"], obj["metadata"]["uid"]
        source = self.kube.pod(ns, spec["sourcePod"])
        cid = validate_workload(source)
        source_node, target_node = source["spec"]["nodeName"], spec["targetNode"]
        if source_node == target_node:
            raise ValueError("source and target must differ")
        for node in (source_node, target_node):
            if node not in self.config["nodes"]:
                raise ValueError(f"node {node} is not configured for SSH")
            n = self.kube.run("get", "node", node, "-o", "json")
            if n.get("spec", {}).get("unschedulable") or not any(c["type"] == "Ready" and c["status"] == "True" for c in n.get("status", {}).get("conditions", [])):
                raise ValueError(f"node {node} is not ready/schedulable")
            if any(t["effect"] in ("NoSchedule", "NoExecute") for t in n.get("spec", {}).get("taints", [])):
                raise ValueError(f"v1 requires an untainted migration node: {node}")
            for key, value in source["spec"].get("nodeSelector", {}).items():
                if n["metadata"].get("labels", {}).get(key) != value:
                    raise ValueError(f"node selector mismatch on {node}")
        target = self.kube.run("get", "node", target_node, "-o", "json")
        allocated = self.kube.run("get", "pods", "-A", "--field-selector", "spec.nodeName=" + target_node, "-o", "json")["items"]
        allocated = [p for p in allocated if p.get("status", {}).get("phase") not in ("Failed", "Succeeded")]
        capacity = target["status"]["allocatable"]
        if len(allocated) + 1 > int(capacity["pods"]):
            raise ValueError("target Pod capacity exhausted")
        for resource in ("cpu", "memory", "ephemeral-storage"):
            if sum((requested(p, resource) for p in allocated), Decimal(0)) + requested(source, resource) > quantity(capacity[resource]):
                raise ValueError(f"target has insufficient requested-resource capacity: {resource}")
        a, b = self.nodes.run(source_node, "preflight"), self.nodes.run(target_node, "preflight")
        if a["arch"] not in self.config.get("allowedArchitectures", ["riscv64"]):
            raise ValueError("source CPU architecture is outside configured migration scope")
        for field in ("arch", "kernel", "cgroup", "containerd", "runc", "criu"):
            if a[field] != b[field]:
                raise ValueError(f"node environment mismatch: {field}")
        description = self.nodes.run(source_node, "describe-source", cid, source["metadata"]["uid"])
        self.nodes.run(target_node, "prepare-image", description["imageRef"])
        original_uid = source["metadata"]["uid"]
        source = self.kube.pod(ns, spec["sourcePod"])
        if source["metadata"]["uid"] != original_uid or validate_workload(source) != cid:
            raise ValueError("source changed during environment preflight")
        annotations = source["metadata"].get("annotations", {})
        if annotations.get(LOCK) not in (None, uid):
            raise ValueError("source is locked by another migration")
        obj = self.update(obj, "Validating", sourceUID=source["metadata"]["uid"], sourcePod=spec["sourcePod"])
        self.kube.patch_pod(source, [{"op": "add", "path": "/metadata/annotations/migration.riscv.io~1lock", "value": uid}])
        name = source["metadata"]["name"][:45].rstrip("-") + "-mv-" + uid[:8]
        self.update(obj, "Checkpointing", sourceUID=source["metadata"]["uid"], containerID=cid,
                    container=source["spec"]["containers"][0]["name"], sourceNode=source_node,
                    targetPod=name, targetNode=target_node, templateSource=source, environment=a)

    def phase_Checkpointing(self, obj):
        s, m = obj["status"], obj["metadata"]
        source = self.kube.pod(m["namespace"], obj["spec"]["sourcePod"])
        if not source or source["metadata"]["uid"] != s["sourceUID"] or source["metadata"].get("annotations", {}).get(LOCK) != m["uid"]:
            raise ValueError("source identity/lock changed before checkpoint")
        manifest = self.nodes.run(s["sourceNode"], "checkpoint", m["uid"], m["namespace"], s["sourceUID"], s["container"], s["containerID"])
        self.update(obj, "Transferring", checkpoint=manifest, checkpointFinishedAt=manifest["finished"])

    def phase_Transferring(self, obj):
        s = obj["status"]
        self.nodes.transfer(s["sourceNode"], s["targetNode"], s["checkpoint"])
        self.update(obj, "Restoring")

    def phase_Restoring(self, obj):
        ns, s, uid = obj["metadata"]["namespace"], obj["status"], obj["metadata"]["uid"]
        pod = self.kube.pod(ns, s["targetPod"])
        if not pod:
            pod = self.kube.run("create", "-f", "-", "-o", "json", data=target_template(s["templateSource"], obj))
        if not any(o["uid"] == uid for o in pod["metadata"].get("ownerReferences", [])):
            raise ValueError("target Pod name belongs to another transaction")
        self.nodes.run(s["targetNode"], "grant", data={"id": uid, "namespace": ns, "pod": s["targetPod"],
                       "podUID": pod["metadata"]["uid"], "container": s["container"], "sha256": s["checkpoint"]["sha256"],
                       "expires": int(time.time()) + int(self.config.get("restoreTimeoutSeconds", 600))})
        if not pod["spec"].get("nodeName"):
            binding = {"apiVersion": "v1", "kind": "Binding", "metadata": {"name": s["targetPod"], "namespace": ns, "uid": pod["metadata"]["uid"]},
                       "target": {"apiVersion": "v1", "kind": "Node", "name": s["targetNode"]}}
            self.kube.run("create", "--raw", f"/api/v1/namespaces/{ns}/pods/{s['targetPod']}/binding", "-f", "-", data=binding)
        elif pod["spec"]["nodeName"] != s["targetNode"]:
            raise ValueError("target Pod bound to an unexpected node")
        self.update(obj, "WaitingForRestore", targetUID=pod["metadata"]["uid"], restoreStartedAt=now())

    def phase_WaitingForRestore(self, obj):
        s, ns = obj["status"], obj["metadata"]["namespace"]
        pod = self.kube.pod(ns, s["targetPod"])
        if not pod or pod["metadata"]["uid"] != s["targetUID"]:
            raise ValueError("target Pod disappeared or changed identity")
        if pod.get("status", {}).get("phase") in ("Failed", "Succeeded"):
            raise ValueError("target exited before running-state verification; inspect retained checkpoint")
        statuses = pod.get("status", {}).get("containerStatuses", [])
        if pod.get("status", {}).get("phase") == "Running" and statuses and statuses[0].get("state", {}).get("running"):
            # Verify runtime metadata says it was restored, not merely started.
            self.nodes.run(s["targetNode"], "verify", statuses[0]["containerID"].removeprefix("containerd://"), s["targetUID"])
            self.update(obj, "Committing", targetRunningAt=now(), message="runtime restored=true verified; application-level continuity still requires the dedicated test")
            return
        started = datetime.fromisoformat(s["restoreStartedAt"]).timestamp()
        if time.time() - started > int(self.config.get("restoreTimeoutSeconds", 600)):
            raise TimeoutError("target restore timed out; original state retained")

    def phase_Committing(self, obj):
        s, ns = obj["status"], obj["metadata"]["namespace"]
        target = self.kube.pod(ns, s["targetPod"])
        if not target or target["metadata"]["uid"] != s["targetUID"] or target.get("status", {}).get("phase") != "Running":
            raise ValueError("restored target stopped or changed before commit")
        source = self.kube.pod(ns, obj["spec"]["sourcePod"])
        if source:
            if source["metadata"]["uid"] != s["sourceUID"]:
                raise ValueError("source Pod name was reused; refusing deletion")
            self.kube.delete_pod(ns, source["metadata"]["name"], s["sourceUID"])
            return
        owners = target["metadata"].get("ownerReferences", [])
        if any(o["uid"] == obj["metadata"]["uid"] for o in owners):
            self.kube.patch_pod(target, [{"op": "add", "path": "/metadata/ownerReferences",
                                       "value": [o for o in owners if o["uid"] != obj["metadata"]["uid"]]}])
        updated = self.update(obj, "Succeeded", completedAt=now(), message="stateful stop-and-copy migration completed; retained archives are not automatically deleted")
        self.kube.finalizer(updated, False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    # One controller process per installation; systemd manages restarts.
    import fcntl
    lock = open(config.get("lockFile", "/run/rv-migration-controller.lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    kube = Kube(config.get("kubeconfig"))
    reconciler = Controller(kube, SSH(config), config)
    while True:
        try:
            for obj in kube.run("get", "nodemigrations", "-A", "-o", "json")["items"]:
                reconciler.step(obj)
        except Exception as error:
            print(f"{now()} reconciliation error: {error}", file=sys.stderr, flush=True)
            if args.once:
                raise
        if args.once:
            break
        time.sleep(int(config.get("pollSeconds", 3)))


if __name__ == "__main__":
    main()
