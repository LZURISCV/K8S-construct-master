#!/usr/bin/env python3
from common import parser, require, run, template

def test(c):
    node = c.nodes[0]; taint = c.taint(node)
    t = template([node]); t["tolerations"] = [dict(taint, operator="Equal")]
    placed = c.running(c.apply("allowed", spec=t))[0]
    require(placed["nodeId"] == node, "tolerated workload did not run on the tainted node")
    return {"taint": taint, "tolerations": t["tolerations"], "runningPod": placed}

if __name__ == "__main__":
    raise SystemExit(run("3-2-0007", parser("3-2-0007", "taint with toleration").parse_args(), test))
