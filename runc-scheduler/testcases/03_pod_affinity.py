#!/usr/bin/env python3
from common import parser, require, run, template

def test(c):
    t = template(c.nodes); t["labels"] = {"anchor": c.prefix}
    anchor = c.running(c.apply("anchor", spec=t))[0]
    t = template(c.nodes)
    t["podAffinity"] = [{"selector": {"anchor": c.prefix}, "topologyKey": "rcs.node"}]
    follower = c.running(c.apply("affinity", spec=t))[0]
    require(anchor["nodeId"] == follower["nodeId"], "Pod affinity did not colocate containers")
    t = template(c.nodes); t["nodeSelector"] = {"rcs.node": anchor["nodeId"]}
    outline = c.running(c.apply("outline-node-match", spec=t))[0]
    require(outline["nodeId"] == anchor["nodeId"], "outline node matching failed")
    return {"anchor": anchor, "follower": follower, "outlineNodeMatch": outline}

if __name__ == "__main__":
    raise SystemExit(run("3-2-0003", parser("3-2-0003", "Pod affinity and outline node matching").parse_args(), test))
