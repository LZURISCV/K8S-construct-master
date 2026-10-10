#!/usr/bin/env python3
"""Single active host controller; modules are independently selectable."""
import argparse
import json
import sys
import time
from common import Kube, now
from resource import Resources
from scenario import Scenarios, Autoscaler
from network import Networks

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",required=True)
    p.add_argument("--once",action="store_true")
    a=p.parse_args()
    config=json.load(open(a.config,encoding="utf-8"))
    import fcntl
    lock=open(config.get("lockFile","/run/rv-platform-controller.lock"),"w")
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    k=Kube(config.get("kubeconfig"))
    resource=Resources(k); scene=Scenarios(k,config); auto=Autoscaler(k,config); net=Networks(k,config)
    modules=config.get("modules",["resource","network-host","scenario"])
    handlers=[]
    if "resource" in modules: handlers += [("resourceclaims",resource.claim),("nodeprofiles",resource.profile)]
    if "network-host" in modules: handlers += [("hostnetworkworkloads",net.step)]
    if "scenario" in modules: handlers += [("scenariopolicies",scene.step),("resourcepolicies",auto.step)]
    while True:
        for kind,handler in handlers:
            try: objects=k.items(kind)
            except Exception as error:
                print(now(),kind,error,file=sys.stderr,flush=True); continue
            for obj in objects:
                if obj["metadata"].get("deletionTimestamp") and kind!="hostnetworkworkloads": continue
                try: handler(obj)
                except Exception as error:
                    print(now(),kind,obj["metadata"]["name"],error,file=sys.stderr,flush=True)
                    try:
                        fresh=k.get(kind,obj["metadata"]["name"],obj["metadata"]["namespace"])
                        state={**fresh.get("status",{}),"phase":"Error","message":str(error)[:2000],"consecutive":0,"lowSinceEpoch":None}
                        k.status(fresh,state)
                    except Exception as nested: print(nested,file=sys.stderr,flush=True)
        if a.once: break
        time.sleep(int(config.get("pollSeconds",10)))

if __name__=="__main__": main()

