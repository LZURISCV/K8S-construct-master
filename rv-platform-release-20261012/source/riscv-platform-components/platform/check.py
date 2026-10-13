#!/usr/bin/env python3
"""Read cluster readiness and record versions; no changes to cluster state."""
import json
import pathlib
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent/"control"))
from common import Kube
def main():
    k=Kube();nodes=k.items("nodes");errors=[]
    for node in nodes:
        info=node.get("status",{}).get("nodeInfo",{})
        if info.get("architecture")!="riscv64":errors.append(node["metadata"]["name"]+": architecture is not riscv64")
        if not any(c["type"]=="Ready" and c["status"]=="True" for c in node.get("status",{}).get("conditions",[])):
            errors.append(node["metadata"]["name"]+": not Ready")
    required=["nodemigrations.migration.riscv.io","resourceclaims.platform.riscv.io","resourcepolicies.platform.riscv.io",
              "nodeprofiles.platform.riscv.io","hostnetworkworkloads.platform.riscv.io","scenariopolicies.platform.riscv.io","mpijobs.kubeflow.org"]
    for name in required:
        try:k.get("crd",name)
        except Exception as e:errors.append(str(e))
    data={"versions":k.call("version","-o","json"),"nodes":[{"name":n["metadata"]["name"],
          "capacity":n.get("status",{}).get("capacity",{}),"nodeInfo":n.get("status",{}).get("nodeInfo",{})} for n in nodes],
          "errors":errors,"scope":"cluster/node/API readiness; not hardware acceptance"}
    print(json.dumps(data,indent=2))
    if errors:raise SystemExit(1)
if __name__=="__main__":main()

