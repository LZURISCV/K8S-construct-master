#!/usr/bin/env python3
from common import parser, run, template

def test(c):
    node = c.nodes[0]; taint = c.taint(node)
    name = c.apply("blocked", spec=template([node]))
    return {"taint": taint, "pendingPods": c.pending(name)}

if __name__ == "__main__":
    raise SystemExit(run("3-2-0006", parser("3-2-0006", "taint without toleration").parse_args(), test))
