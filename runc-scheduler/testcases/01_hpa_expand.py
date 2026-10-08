#!/usr/bin/env python3
import time
from common import autoscaler, high_metrics, hpa_spec, parser, pods, require, run

def test(c):
    name = c.apply("expand", spec=hpa_spec(c.nodes[0]), autoscaler=autoscaler())
    first = c.running(name)
    enabled = set()
    end = time.monotonic() + c.args.timeout
    peak = None
    while time.monotonic() < end:
        state = c.api.state()
        for pod in pods(state, name, "Running"):
            if pod["id"] not in enabled:
                c.api.exec(pod["id"], ["/usr/local/bin/rcs", "marker", "on"])
                enabled.add(pod["id"])
        peak = high_metrics(state, name, 3)
        if peak and state["workloads"][name]["replicas"] == 3: break
        peak = None
        time.sleep(2)
    require(peak, "HPA did not expand to three running replicas with real memory metrics")
    return {"initialReplicas": len(first), "finalReplicas": 3, "peakMetrics": peak}

if __name__ == "__main__":
    raise SystemExit(run("3-2-0001", parser("3-2-0001", "HPA expansion 1 -> 3").parse_args(), test))
