import importlib.util
import json
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rv_ecosystem_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
health_spec = importlib.util.spec_from_file_location("rv_ecosystem_health", ROOT / "verify.py")
health = importlib.util.module_from_spec(health_spec)
health_spec.loader.exec_module(health)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.offline.k8snode1.json").read_text())
        self.node = {"metadata": {"name": "k8snode1"}, "status": {
            "nodeInfo": {"architecture": "riscv64", "operatingSystem": "linux"},
            "conditions": [{"type": "Ready", "status": "True"}]}}

    def test_offline_forces_never_even_if_config_requests_pull(self):
        self.config["imagePullPolicy"] = "Always"
        self.assertEqual(installer.deployment_values(self.config, True)["imagePullPolicy"], "Never")

    def test_offline_requires_node_placement(self):
        self.config.pop("nodeSelector")
        with self.assertRaises(ValueError):
            installer.deployment_values(self.config, True)

    def test_online_mode_preserves_image_pull_policy(self):
        self.config["imagePullPolicy"] = "Always"
        self.assertEqual(installer.deployment_values(self.config)["imagePullPolicy"], "Always")

    def test_legacy_grafana_settings_do_not_create_a_dependency(self):
        self.config.update(grafanaImage="unused/image", grafana={"command": ["unused"]})
        values = installer.deployment_values(self.config, True)
        self.assertNotIn("grafanaImage", values)
        self.assertNotIn("grafana", values)

    def test_no_matching_nodes_fails_before_deployment(self):
        with patch.object(installer, "run", return_value=json.dumps({"items": []})):
            with self.assertRaises(ValueError):
                installer.check_offline_nodes(installer.deployment_values(self.config, True))

    def test_wrong_architecture_fails_before_deployment(self):
        self.node["status"]["nodeInfo"]["architecture"] = "amd64"
        with patch.object(installer, "run", return_value=json.dumps({"items": [self.node]})):
            with self.assertRaises(ValueError):
                installer.check_offline_nodes(installer.deployment_values(self.config, True))

    def test_not_ready_fails_before_deployment(self):
        self.node["status"]["conditions"][0]["status"] = "False"
        with patch.object(installer, "run", return_value=json.dumps({"items": [self.node]})):
            with self.assertRaises(ValueError):
                installer.check_offline_nodes(installer.deployment_values(self.config, True))

    def test_ready_riscv_node_is_accepted(self):
        with patch.object(installer, "run", return_value=json.dumps({"items": [self.node]})) as runner:
            installer.check_offline_nodes(installer.deployment_values(self.config, True))
            self.assertIn("kubernetes.io/hostname=k8snode1", runner.call_args.args)


class HealthTests(unittest.TestCase):
    def response(self, url, headers=None):
        if url.endswith("/api/v1/targets"):
            return {"data": {"activeTargets": [{"labels": {"job": "rv-nodes", "instance": "node:9108"}, "health": "up"}]}}
        raise AssertionError("unexpected non-Prometheus endpoint: " + url)

    def sample(self, url, expression):
        return 1000 if expression.startswith("rv_node_sample_timestamp_seconds") else 0.25

    def test_real_health_contract(self):
        with patch.object(health, "get", side_effect=self.response), patch.object(health, "query", side_effect=self.sample), patch.object(health.time, "time", return_value=1000):
            self.assertTrue(health.verify("http://prom", ["node:9108"])["passed"])

    def test_scrape_up_with_stale_exporter_is_rejected(self):
        with patch.object(health, "get", side_effect=self.response), patch.object(health, "query", side_effect=self.sample), patch.object(health.time, "time", return_value=1200):
            with self.assertRaises(RuntimeError):
                health.verify("http://prom", ["node:9108"])

    def test_missing_expected_node_is_rejected(self):
        with patch.object(health, "get", side_effect=self.response):
            with self.assertRaises(RuntimeError):
                health.verify("http://prom", ["other:9108"])


    def test_failed_scrape_is_rejected(self):
        targets = self.response("http://prom/api/v1/targets")
        targets["data"]["activeTargets"][0].update(health="down", lastError="connection refused")
        with patch.object(health, "get", return_value=targets):
            with self.assertRaises(RuntimeError):
                health.verify("http://prom", ["node:9108"])


if __name__ == "__main__":
    unittest.main()
