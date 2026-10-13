#!/usr/bin/env python3
"""Real Prometheus scrape health and fresh exporter samples; no browser required."""
import importlib.util
import os
import pathlib
from cases import Case

spec = importlib.util.spec_from_file_location(
    "rv_monitoring_verify", pathlib.Path(__file__).resolve().parents[1] / "ecosystem" / "verify.py")
monitoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitoring)


def main():
    c = Case("ecosystem", require_image=False)
    error = None
    try:
        expected = [item.strip() for item in os.environ.get("PROMETHEUS_TARGETS", "").split(",") if item.strip()]
        result = monitoring.verify(os.environ["PROMETHEUS_URL"], expected)
        c.record("real monitoring health", nodeTargets=len(result["nodeSamples"]), **result)
    except Exception as e:
        error = e
    finally:
        c.finish(error)
    if error:
        raise error


if __name__ == "__main__":
    main()
