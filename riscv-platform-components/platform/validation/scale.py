#!/usr/bin/env python3
"""Real Pod scale test. Does not manufacture worker or network throughput evidence."""
import argparse
import concurrent.futures
import json
import pathlib
import sys
import time
import uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"control"))
from common import Kube, quantity

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--image",required=True);p.add_argument("--pods",type=int,required=True)
    p.add_argument("--clients",type=int,default=8);p.add_argument("--timeout",type=int,default=1800)
    p.add_argument("--mode",choices=["real","kwok-control-only"],default="real");p.add_argument("--keep",action="store_true")
    p.add_argument("--output",default="scale.json");a=p.parse_args()
    if not 1<=a.pods<=100000 or not 1<=a.clients<=128: raise ValueError("invalid scale")
    k=Kube(); ns="rv-scale-"+uuid.uuid4().hex[:10]
    k.create({"apiVersion":"v1","kind":"Namespace","metadata":{"name":ns}})
    nodes=k.items("nodes")
    if a.mode=="real" and any("kwok" in json.dumps(n["metadata"].get("labels",{})).lower() for n in nodes):
        raise ValueError("KWOK nodes found: real test must run on separate real-node cluster")
    start=time.time(); completed={}
    def create(index):
        spec={"containers":[{"name":"workload","image":a.image,"command":["sleep","86400"],
          "resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"100m","memory":"32Mi"}}}],
          "automountServiceAccountToken":False}
        if a.mode=="real": spec["nodeSelector"]={"kubernetes.io/arch":"riscv64"}
        obj=k.create({"apiVersion":"v1","kind":"Pod","metadata":{"name":"p-"+str(index),"namespace":ns},"spec":spec})
        return obj["metadata"]["uid"]
    error=None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.clients) as pool:
            list(pool.map(create,range(a.pods)))
        create_done=time.time()
        while time.time()-start<a.timeout:
            pods=k.items("pods",ns)
            for obj in pods:
                uid=obj["metadata"]["uid"]
                if uid not in completed and any(c["type"]=="Ready" and c["status"]=="True" for c in obj.get("status",{}).get("conditions",[])):
                    completed[uid]={"name":obj["metadata"]["name"],"readyAfterSeconds":time.time()-start,"node":obj["spec"].get("nodeName")}
            if len(completed)==a.pods: break
            time.sleep(3)
        if len(completed)!=a.pods: error="not all Pods Ready by timeout"
        result={"operation":"scale","mode":a.mode,"requestedPods":a.pods,"readyPods":len(completed),
          "createSeconds":create_done-start,"totalSeconds":time.time()-start,"status":"PASS" if not error else "FAIL",
          "nodes":[{"name":n["metadata"]["name"],"capacity":n.get("status",{}).get("capacity",{}),
             "nodeInfo":n.get("status",{}).get("nodeInfo",{})} for n in nodes],
          "observedWorkerNodes":sorted(set(x["node"] for x in completed.values())),
          "samples":list(completed.values()),"image":a.image,"error":error,
          "scope":"control-plane-only" if a.mode!="real" else "actual-pod-deployment; throughput and latency measured separately"}
        pathlib.Path(a.output).write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps({key:value for key,value in result.items() if key!="samples"},indent=2))
    finally:
        if not a.keep: k.call("delete","namespace",ns,"--wait=false","-o","json")
    if error: raise SystemExit(1)
if __name__=="__main__": main()

