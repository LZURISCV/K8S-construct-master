#!/usr/bin/env python3
from common import parser, require, run, template

def test(c):
    t = template([c.nodes[0]])
    placed = c.running(c.apply("node-affinity", spec=t))[0]
    require(placed["nodeId"] == c.nodes[0], "nodeAffinity placed on wrong node")
    t = template([c.prefix + "-missing"])
    blocked = c.pending(c.apply("node-mismatch", spec=t))
    return {"matchingNode": placed, "nonmatchingRule": blocked}

if __name__ == "__main__":
    raise SystemExit(run("3-2-0012", parser("3-2-0012", "required node affinity").parse_args(), test))
