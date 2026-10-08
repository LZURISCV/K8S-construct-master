#!/usr/bin/env python3
from common import parser, require, run, template

def test(c):
    t = template(c.nodes); t["nodeSelector"] = {"rcs.node": c.prefix + "-missing"}
    blocked = c.pending(c.apply("outline-mismatch", spec=t))
    selected = c.nodes[:2]
    t = template(selected); t["labels"] = {"spread": c.prefix}
    t["podAntiAffinity"] = [{"selector": {"spread": c.prefix}, "topologyKey": "rcs.node"}]
    name = c.apply("spread", spec=t, replicas=len(selected))
    placed = c.running(name, len(selected))
    require(len({p["nodeId"] for p in placed}) == len(selected), "anti-affinity failed to spread containers")
    c.api.call("POST", "/v1/scale/" + name, {"replicas": len(selected) + 1})
    excess = c.pending(name)
    evidence = {"outlineMismatch": blocked, "placements": placed, "excessReplica": excess,
                "singleNodeExclusion": "PASS"}
    if len(selected) < 2:
        evidence.update(status="PARTIAL", crossNodeSpread="SKIP", reason="needs two deployment nodes")
    else: evidence["crossNodeSpread"] = "PASS"
    return evidence

if __name__ == "__main__":
    raise SystemExit(run("3-2-0004", parser("3-2-0004", "anti-affinity and outline mismatch Pending").parse_args(), test))
