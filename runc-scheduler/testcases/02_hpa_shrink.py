#!/usr/bin/env python3
from common import autoscaler, high_metrics, hpa_spec, parser, pods, run

def test(c):
    # Prepare three replicas directly; does not execute or depend on expansion case.
    spec = hpa_spec(c.nodes[0])
    name = c.apply("shrink", spec=spec, replicas=3)
    for pod in c.running(name, 3):
        c.api.exec(pod["id"], ["/usr/local/bin/rcs", "marker", "on"])
    peak = c.api.wait(lambda s: high_metrics(s, name, 3), timeout=c.args.timeout, description="high memory metrics")
    c.apply("shrink", spec=spec, replicas=3, autoscaler=autoscaler())
    for pod in peak: c.api.exec(pod["id"], ["/usr/local/bin/rcs", "marker", "off"])
    c.api.wait(lambda s: s["workloads"][name]["replicas"] == 1 and len(pods(s, name, "Running")) == 1,
               timeout=c.args.timeout, description="HPA contraction 3 -> 1")
    return {"initialReplicas": 3, "finalReplicas": 1, "highMetrics": peak,
            "finalMetrics": pods(c.api.state(), name, "Running")}

if __name__ == "__main__":
    raise SystemExit(run("3-2-0002", parser("3-2-0002", "HPA contraction 3 -> 1").parse_args(), test))
