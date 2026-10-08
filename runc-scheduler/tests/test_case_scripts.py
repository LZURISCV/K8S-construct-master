"""Local checks for case independence, reporting, cleanup and MPI selection."""
import argparse
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "testcases"))
import common
import mpi_common

def load(name):
    spec = importlib.util.spec_from_file_location("case_" + name, ROOT / "testcases" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Cases(unittest.TestCase):
    def test_mpi_each_entry_selects_only_its_collective(self):
        for name, mode in [("08_mpi_broadcast", "broadcast"), ("09_mpi_reduce", "reduce"), ("10_mpi_allgather", "allgather")]:
            module = load(name)
            with patch.object(module, "collective", return_value={}) as selected:
                c = object(); module.test(c)
                selected.assert_called_once_with(c, mode)

    def test_shrink_sets_up_three_replicas_without_expansion(self):
        module = load("02_hpa_shrink")
        c = MagicMock(); c.nodes = ["worker1"]; c.args.timeout = 360
        c.apply.return_value = "test-shrink"
        c.running.return_value = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        c.api.wait.side_effect = [[{"id": "a"}, {"id": "b"}, {"id": "c"}], True]
        c.api.state.return_value = {"workloads": {"test-shrink": {"generation": 1}}, "pods": {}}
        result = module.test(c)
        self.assertEqual([call.kwargs["replicas"] for call in c.apply.call_args_list], [3, 3])
        self.assertEqual(result["finalReplicas"], 1)
        self.assertEqual([call.args[1][-1] for call in c.api.exec.call_args_list], ["on"] * 3 + ["off"] * 3)

    def test_taint_without_toleration_does_not_create_allowed_workload(self):
        c = MagicMock(); c.nodes = ["worker1"]
        load("06_taint_without_toleration").test(c)
        c.apply.assert_called_once()
        self.assertNotIn("tolerations", c.apply.call_args.kwargs["spec"])
        c.running.assert_not_called()

    def test_toleration_case_prepares_own_taint(self):
        c = MagicMock(); c.nodes = ["worker1"]
        c.taint.return_value = {"key": "t", "value": "x", "effect": "NoSchedule"}
        c.running.return_value = [{"nodeId": "worker1"}]
        load("07_taint_with_toleration").test(c)
        c.taint.assert_called_once_with("worker1")
        self.assertEqual(c.apply.call_args.kwargs["spec"]["tolerations"][0]["operator"], "Equal")
        c.pending.assert_not_called()

    def test_failed_test_still_captures_state_cleans_up_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(timeout=30, url="http://unused", token_file="unused", ca=None, report=str(pathlib.Path(tmp) / "one.json"))
            c = MagicMock(); c.nodes = ["worker1"]; c.initial = {"nodes": {"worker1": {"cgroupMode": "v1"}}}
            api = MagicMock(); api.state.return_value = {"pods": {"test": {"log": "evidence"}}}
            def fail(_): raise RuntimeError("test failed")
            with patch.object(common, "API", return_value=api), patch.object(common, "Case", return_value=c):
                self.assertEqual(common.run("3-2-0001", args, fail), 1)
            c.cleanup.assert_called_once()
            report = json.loads(pathlib.Path(args.report).read_text())
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("stateBeforeCleanup", report)

    def test_cleanup_failure_overrides_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(timeout=30, url="http://unused", token_file="unused", ca=None, report=str(pathlib.Path(tmp) / "one.json"))
            c = MagicMock(); c.nodes = ["worker1"]; c.initial = {"nodes": {"worker1": {"cgroupMode": "v1"}}}
            c.cleanup.side_effect = RuntimeError("cleanup failed")
            api = MagicMock(); api.state.return_value = {}
            with patch.object(common, "API", return_value=api), patch.object(common, "Case", return_value=c):
                self.assertEqual(common.run("3-2-0007", args, lambda _: {}), 1)
            report = json.loads(pathlib.Path(args.report).read_text())
            self.assertEqual(report["cleanupError"], "cleanup failed")

    def test_case_cleanup_restores_taint_even_if_deletion_fails(self):
        c = object.__new__(common.Case)
        c.api = MagicMock(); c.api.delete.side_effect = RuntimeError("unavailable")
        c.names = ["test"]; c.changed_taints = {"worker1"}
        c.initial = {"nodes": {"worker1": {"taints": []}}}; c.args = argparse.Namespace(timeout=1)
        with self.assertRaisesRegex(RuntimeError, "unavailable"): c.cleanup()
        c.api.patch.assert_called_once_with("worker1", {"taints": []})

    def test_mpi_wrong_result_is_rejected(self):
        c = MagicMock(); c.nodes = ["worker1"]
        c.initial = {"nodes": {"worker1": {"id": "worker1", "architecture": "riscv64", "address": "192.168.10.11"}}}
        c.args.ranks_per_node = 2; c.args.job_timeout = 900; c.args.interface = ""
        c.api.wait.return_value = {"id": "pod", "log": 'RCS_RESULT {"test":"reduce","ranks":2,"passed":true}'}
        with self.assertRaisesRegex(RuntimeError, "unexpected test"): mpi_common.MPI(c).job("broadcast")

if __name__ == "__main__": unittest.main()
