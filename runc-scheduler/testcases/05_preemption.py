#!/usr/bin/env python3
import copy
from common import parser, pods, require, run, template

def test(c):
    node = c.nodes[0]
    request = int(c.initial["nodes"][node]["capacity"]["cpuMillis"] * .7)
    t = template([node]); t["requests"]["cpuMillis"] = request; t["limits"]["cpuMillis"] = request
    t["priority"] = 10
    low = c.apply("low", spec=t); victim = c.running(low)[0]
    t = copy.deepcopy(t); t["priority"] = 100
    winner = c.running(c.apply("high", spec=t))[0]
    state = c.api.state()
    require(state["pods"][victim["id"]]["phase"] == "Removed", "victim stop not confirmed before resource reuse")
    require(not pods(state, low, "Running"), "low priority workload running alongside the winner")
    return {"victim": state["pods"][victim["id"]], "winner": winner, "requestedCPUMillis": request}

if __name__ == "__main__":
    raise SystemExit(run("3-2-0005", parser("3-2-0005", "priority preemption").parse_args(), test))
