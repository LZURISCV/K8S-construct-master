import copy
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("migration_controller", pathlib.Path(__file__).resolve().parents[1] / "controller.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def pod():
    return {"metadata": {"name": "counter", "namespace": "demo", "uid": "source-uid", "resourceVersion": "1", "annotations": {c.OPT_IN: "true"}},
            "spec": {"nodeName": "node-a", "restartPolicy": "Never", "automountServiceAccountToken": False,
                     "containers": [{"name": "counter", "image": "base@sha256:123", "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}}}]},
            "status": {"phase": "Running", "containerStatuses": [{"containerID": "containerd://" + "a" * 64, "state": {"running": {"startedAt": "2026-01-01T00:00:00Z"}}}]}}


def request(phase="Pending"):
    spec = {"sourcePod": "counter", "targetNode": "node-b"}
    return {"apiVersion": "migration.riscv.io/v1alpha1", "kind": "NodeMigration",
            "metadata": {"name": "move", "namespace": "demo", "uid": "request-uid", "resourceVersion": "1"},
            "spec": spec, "status": {"phase": phase, "specHash": c.spec_hash(spec)}}


class FakeKube:
    def __init__(self, obj):
        self.obj = obj
        self.pods = {"counter": pod()}
        self.deleted = []

    def pod(self, ns, name):
        return copy.deepcopy(self.pods.get(name))

    def status(self, obj, status):
        self.obj["status"] = copy.deepcopy(status)
        return copy.deepcopy(self.obj)

    def finalizer(self, obj, add):
        self.obj["metadata"]["finalizers"] = [c.FINALIZER] if add else []
        return copy.deepcopy(self.obj)

    def patch_pod(self, p, changes):
        actual = self.pods[p["metadata"]["name"]]
        for change in changes:
            if change["path"].endswith("~1lock"):
                if change["op"] == "remove":
                    actual["metadata"]["annotations"].pop(c.LOCK)
                else:
                    actual["metadata"]["annotations"][c.LOCK] = change["value"]
            elif change["path"].endswith("ownerReferences"):
                actual["metadata"]["ownerReferences"] = change["value"]
        return copy.deepcopy(actual)

    def delete_pod(self, ns, name, uid):
        if self.pods[name]["metadata"]["uid"] != uid:
            raise ValueError("UID mismatch")
        self.deleted.append((name, uid)); del self.pods[name]

    def run(self, *args, data=None, **kwargs):
        if args[:2] == ("get", "node"):
            return {"metadata": {"labels": {}}, "status": {"conditions": [{"type": "Ready", "status": "True"}],
                    "allocatable": {"cpu": "8", "memory": "16Gi", "ephemeral-storage": "100Gi", "pods": "100"}}}
        if args[:2] == ("get", "pods"):
            return {"items": []}
        if args[0] == "create" and "--raw" not in args:
            new = copy.deepcopy(data); new["metadata"].update(uid="target-uid", resourceVersion="1")
            self.pods[new["metadata"]["name"]] = new
            return copy.deepcopy(new)
        if args[0] == "create" and "--raw" in args:
            self.pods[data["metadata"]["name"]]["spec"]["nodeName"] = data["target"]["name"]
            return {}
        raise AssertionError(args)


class FakeNodes:
    def __init__(self):
        self.calls = []; self.transfer_error = False

    def run(self, node, *args, data=None):
        self.calls.append((node, args, data))
        if args[0] == "preflight":
            return {"arch": "riscv64", "kernel": "6.6", "cgroup": "v1", "containerd": "2.1.5", "runc": "1.2", "criu": "4"}
        if args[0] == "checkpoint":
            return {"id": "request-uid", "sha256": "f" * 64, "bytes": 100, "finished": c.now()}
        if args[0] == "describe-source":
            return {"imageRef": "example.invalid/counter@sha256:" + "f" * 64}
        return {"restored": True}

    def transfer(self, source, target, manifest):
        if self.transfer_error:
            raise RuntimeError("injected transfer failure")
        return manifest


class MigrationTests(unittest.TestCase):
    def setup_controller(self):
        obj = request(); kube = FakeKube(obj); nodes = FakeNodes()
        return obj, kube, nodes, c.Controller(kube, nodes, {"nodes": {"node-a": {}, "node-b": {}}})

    def test_rejects_controller_owned_pod(self):
        p = pod(); p["metadata"]["ownerReferences"] = [{"kind": "ReplicaSet"}]
        with self.assertRaisesRegex(ValueError, "standalone"):
            c.validate_workload(p)

    def test_rejects_volumes_and_restart(self):
        for field, value in (("volumes", [{"name": "data"}]), ("restartPolicy", "Always")):
            p = pod(); p["spec"][field] = value
            with self.assertRaises(ValueError):
                c.validate_workload(p)

    def test_real_state_machine_and_owner_detachment(self):
        obj, kube, nodes, controller = self.setup_controller()
        for _ in range(5):
            controller.step(copy.deepcopy(obj))
        self.assertEqual(obj["status"]["phase"], "WaitingForRestore")
        target = kube.pods[obj["status"]["targetPod"]]
        target["status"] = pod()["status"]
        controller.step(copy.deepcopy(obj))
        self.assertEqual(obj["status"]["phase"], "Committing")
        controller.step(copy.deepcopy(obj)); controller.step(copy.deepcopy(obj))
        self.assertEqual(obj["status"]["phase"], "Succeeded")
        self.assertEqual(kube.deleted, [("counter", "source-uid")])
        self.assertEqual(target["metadata"]["ownerReferences"], [])
        self.assertEqual(obj["metadata"]["finalizers"], [])
        grant = next(call[2] for call in nodes.calls if call[1][0] == "grant")
        self.assertEqual(grant["podUID"], "target-uid")

    def test_transfer_failure_retains_source_and_finalizer(self):
        obj, kube, nodes, controller = self.setup_controller()
        for _ in range(3): controller.step(copy.deepcopy(obj))
        nodes.transfer_error = True
        controller.step(copy.deepcopy(obj))
        self.assertEqual(obj["status"]["phase"], "RecoveryRequired")
        self.assertIn("counter", kube.pods)
        self.assertIn(c.FINALIZER, obj["metadata"]["finalizers"])
        self.assertEqual(kube.pods["counter"]["metadata"]["annotations"][c.LOCK], "request-uid")
        self.assertFalse(kube.deleted)

    def test_does_not_delete_a_name_reused_pod(self):
        obj, kube, nodes, controller = self.setup_controller()
        for _ in range(5): controller.step(copy.deepcopy(obj))
        kube.pods[obj["status"]["targetPod"]]["status"] = pod()["status"]
        controller.step(copy.deepcopy(obj))
        kube.pods["counter"]["metadata"]["uid"] = "new-uid"
        controller.step(copy.deepcopy(obj))
        self.assertEqual(obj["status"]["phase"], "RecoveryRequired")
        self.assertFalse(kube.deleted)

    def test_spec_change_before_checkpoint_is_rejected(self):
        obj, kube, nodes, controller = self.setup_controller()
        controller.step(copy.deepcopy(obj)); obj["spec"]["targetNode"] = "node-c"
        controller.step(copy.deepcopy(obj))
        self.assertEqual(obj["status"]["phase"], "Failed")
        self.assertFalse(any(call[1][0] == "checkpoint" for call in nodes.calls))

    def test_resource_quantities(self):
        self.assertEqual(c.quantity("100m"), c.Decimal(".1"))
        self.assertEqual(c.quantity("1Gi"), 1024 ** 3)
        self.assertEqual(c.requested(pod(), "memory"), 64 * 1024 ** 2)

    def test_success_finalizer_cleanup_survives_controller_restart(self):
        obj, kube, nodes, controller = self.setup_controller()
        obj["status"]["phase"] = "Succeeded"
        obj["metadata"]["finalizers"] = [c.FINALIZER]
        controller.step(copy.deepcopy(obj))
        self.assertEqual(obj["metadata"]["finalizers"], [])
        self.assertFalse(nodes.calls)

    def test_insufficient_target_capacity_does_not_checkpoint(self):
        obj, kube, nodes, controller = self.setup_controller()
        original_run = kube.run
        def run(*args, **kwargs):
            result = original_run(*args, **kwargs)
            if args[:2] == ("get", "node"):
                result["status"]["allocatable"]["memory"] = "1Mi"
            return result
        kube.run = run
        controller.step(copy.deepcopy(obj)); controller.step(copy.deepcopy(obj))
        self.assertEqual(obj["status"]["phase"], "Failed")
        self.assertFalse(any(call[1][0] == "checkpoint" for call in nodes.calls))

    def test_kubelet_endpoint_update_preserves_quotes_and_is_idempotent(self):
        spec = importlib.util.spec_from_file_location("configure_kubelet", pathlib.Path(__file__).resolve().parents[1] / "configure_kubelet.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        before = 'KUBELET_CONFIG="--config=/etc/kubernetes/config.yaml"\nKUBELET_ARGS="--fail-swap-on=false --container-runtime-endpoint=unix:///run/containerd/containerd.sock"\n'
        after = module.configure(before)
        self.assertTrue(after.endswith('cri.sock"\n'))
        self.assertIn('KUBELET_CONFIG="--config=/etc/kubernetes/config.yaml"', after)
        self.assertIn('--fail-swap-on=false', after)
        self.assertEqual(module.configure(after), after)


if __name__ == "__main__":
    unittest.main()
