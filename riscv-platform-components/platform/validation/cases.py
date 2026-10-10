#!/usr/bin/env python3
"""Independent cluster cases share plumbing. All assertions observe real K8s objects."""
import copy
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"control"))
from common import Kube, quantity, now

class Case:
    def __init__(self,name):
        self.k=Kube(os.environ.get("KUBECONFIG")); self.name=name
        self.ns="rv-test-"+name[:15]+"-"+uuid.uuid4().hex[:8]
        self.image=os.environ["TEST_IMAGE"]; self.timeout=int(os.environ.get("TEST_TIMEOUT","600"))
        self.node=os.environ.get("TEST_NODE")
        self.started=time.time(); self.events=[]; self.cleanups=[]
        self.k.create({"apiVersion":"v1","kind":"Namespace","metadata":{"name":self.ns}})
    def record(self,message,**values): self.events.append({"at":now(),"message":message,**values})
    def wait(self,fn,message,timeout=None):
        start=time.time()
        while time.time()-start<(timeout or self.timeout):
            result=fn()
            if result: self.record(message); return result
            time.sleep(3)
        raise TimeoutError(message)
    def pod(self,name,spec=None,labels=None):
        obj={"apiVersion":"v1","kind":"Pod","metadata":{"name":name,"namespace":self.ns,"labels":labels or {}},
             "spec":{"containers":[{"name":"workload","image":self.image,"command":["python3","-c","import time;time.sleep(86400)"],
                       "resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"1","memory":"128Mi"}}}],
                     "restartPolicy":"Never","automountServiceAccountToken":False}}
        if spec: obj["spec"].update(spec)
        return self.k.create(obj)
    def ready(self,name):
        def check():
            p=self.k.get("pod",name,self.ns)
            if p.get("status",{}).get("phase")=="Failed": raise AssertionError(json.dumps(p["status"]))
            return p if any(c["type"]=="Ready" and c["status"]=="True" for c in p.get("status",{}).get("conditions",[])) else None
        return self.wait(check,name+" Ready")
    def unscheduled(self,name):
        def check():
            p=self.k.get("pod",name,self.ns)
            if p["spec"].get("nodeName"): raise AssertionError("unexpected scheduling")
            return p if any(c["type"]=="PodScheduled" and c["status"]=="False" and c.get("reason")=="Unschedulable"
                            for c in p.get("status",{}).get("conditions",[])) else None
        return self.wait(check,name+" Unschedulable")
    def target(self):
        if not self.node: raise ValueError("set TEST_NODE to a dedicated worker")
        n=self.k.get("node",self.node)
        return {"kubernetes.io/hostname":n["metadata"]["labels"]["kubernetes.io/hostname"]}
    def finish(self,error=None):
        result={"case":self.name,"namespace":self.ns,"startedAtEpoch":self.started,"durationSeconds":time.time()-self.started,
                "status":"FAIL" if error else "PASS","events":self.events,"error":str(error) if error else None,
                "testImage":self.image,"testNode":self.node,"evidence":"real-kubernetes"}
        result["serverVersion"]=self.k.call("version","-o","json").get("serverVersion",{})
        directory=pathlib.Path(os.environ.get("TEST_RESULTS","./platform-test-results")); directory.mkdir(parents=True,exist_ok=True)
        (directory/(self.name+"-"+self.ns[-8:]+".json")).write_text(json.dumps(result,indent=2),encoding="utf-8")
        for fn in reversed(self.cleanups):
            try: fn()
            except Exception as nested: print("cleanup:",nested,file=sys.stderr)
        if os.environ.get("KEEP_TEST_NAMESPACE")!="1":
            subprocess.run(self.k.base+["delete","namespace",self.ns,"--wait=false"],check=True)
        print(json.dumps(result,indent=2))

def affinity(c):
    c.pod("anchor",{"nodeSelector":c.target()},{"app":"anchor"}); anchor=c.ready("anchor")
    c.pod("follower",{"affinity":{"podAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":[
        {"labelSelector":{"matchLabels":{"app":"anchor"}},"topologyKey":"kubernetes.io/hostname"}]}}})
    assert c.ready("follower")["spec"]["nodeName"]==anchor["spec"]["nodeName"]

def exclusion(c):
    c.pod("excluded",{"affinity":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{
      "nodeSelectorTerms":[{"matchExpressions":[{"key":"platform.riscv.io/never-"+c.ns[-8:],"operator":"In","values":["missing"]}]}]}}}})
    c.unscheduled("excluded")

def node_affinity(c):
    match=c.target()
    c.pod("target",{"affinity":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{"nodeSelectorTerms":[
      {"matchExpressions":[{"key":k,"operator":"In","values":[v]} for k,v in match.items()]}]}}}})
    assert c.ready("target")["spec"]["nodeName"]==c.node

def anti_affinity(c):
    c.pod("anchor",{"nodeSelector":c.target()},{"app":"exclusive"}); c.ready("anchor")
    c.pod("anti",{"nodeSelector":c.target(),"affinity":{"podAntiAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":[
      {"labelSelector":{"matchLabels":{"app":"exclusive"}},"topologyKey":"kubernetes.io/hostname"}]}}},{"app":"exclusive"})
    c.unscheduled("anti")

def taint(c,tolerate):
    selector=c.target(); key="platform.riscv.io/test-"+c.ns[-8:]
    c.k.call("taint","node",c.node,key+"=yes:NoSchedule")
    c.cleanups.append(lambda:c.k.call("taint","node",c.node,key+"-"))
    spec={"nodeSelector":selector}
    if tolerate: spec["tolerations"]=[{"key":key,"operator":"Equal","value":"yes","effect":"NoSchedule"}]
    c.pod("tainted",spec)
    if tolerate: assert c.ready("tainted")["spec"]["nodeName"]==c.node
    else: c.unscheduled("tainted")

def preemption(c):
    if os.environ.get("ALLOW_PREEMPTION_TEST")!="1": raise ValueError("set ALLOW_PREEMPTION_TEST=1 on an empty dedicated worker")
    selector=c.target(); node=c.k.get("node",c.node)
    existing=[p for p in c.k.items("pods") if p["spec"].get("nodeName")==c.node and p.get("status",{}).get("phase") not in ("Succeeded","Failed")]
    if any(p["metadata"]["namespace"]!="kube-system" and not any(o["kind"]=="DaemonSet" for o in p["metadata"].get("ownerReferences",[])) for p in existing):
        raise ValueError("preemption test requires no unrelated workload on worker")
    if any(int(p["spec"].get("priority",0)) < -900 for p in existing): raise ValueError("unrelated lower-priority workload present")
    used=sum(sum(quantity(x.get("resources",{}).get("requests",{}).get("cpu","0")) for x in p["spec"].get("containers",[])+p["spec"].get("initContainers",[])) for p in existing)
    available=quantity(node["status"]["allocatable"]["cpu"])-used
    if available<1: raise ValueError("at least one unreserved CPU needed")
    names=[c.ns+"-low",c.ns+"-high"]
    for name,value in zip(names,[-1000,-900]):
        c.k.create({"apiVersion":"scheduling.k8s.io/v1","kind":"PriorityClass","metadata":{"name":name},"value":value,"globalDefault":False,"preemptionPolicy":"PreemptLowerPriority"})
        c.cleanups.append(lambda name=name:c.k.call("delete","priorityclass",name,"-o","json"))
    low=c.pod("low",{"nodeSelector":selector,"priorityClassName":names[0],
        "containers":[{"name":"workload","image":c.image,"command":["sleep","86400"],
         "resources":{"requests":{"cpu":str(available*quantity("0.60")),"memory":"16Mi"}}}]})
    low=c.ready("low"); lowuid=low["metadata"]["uid"]
    c.pod("high",{"nodeSelector":selector,"priorityClassName":names[1],
        "containers":[{"name":"workload","image":c.image,"command":["sleep","86400"],
         "resources":{"requests":{"cpu":str(available*quantity("0.75")),"memory":"16Mi"}}}]})
    c.ready("high")
    c.wait(lambda: (lambda p:not p or p["metadata"].get("deletionTimestamp") or p.get("status",{}).get("phase")=="Failed")(c.k.get("pod","low",c.ns,missing=True)),"low-priority Pod evicted")
    c.record("preemption verified",sourceUID=lowuid)

def hpa(c,shrink):
    program="""import os,time,threading
memory=None
def load():
 global memory
 while True:
  if os.path.exists('/tmp/enable_pressure'):
   if memory is None: memory=bytearray(160*1024*1024)
   for i in range(0,len(memory),4096): memory[i]=(memory[i]+1)%255
  else: memory=None
  time.sleep(.2)
threading.Thread(target=load,daemon=True).start()
while True: time.sleep(1)
"""
    deployment={"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"load","namespace":c.ns},
      "spec":{"replicas":1,"selector":{"matchLabels":{"app":"load"}},"template":{"metadata":{"labels":{"app":"load"}},
        "spec":{"containers":[{"name":"workload","image":c.image,"command":["python3","-c",program],
          "resources":{"requests":{"cpu":"100m","memory":"64Mi"},"limits":{"cpu":"1","memory":"256Mi"}}}]}}}}
    c.k.create(deployment)
    c.k.call("rollout","status","deployment/load","-n",c.ns,"--timeout=300s")
    c.k.create({"apiVersion":"autoscaling/v2","kind":"HorizontalPodAutoscaler","metadata":{"name":"load","namespace":c.ns},
      "spec":{"scaleTargetRef":{"apiVersion":"apps/v1","kind":"Deployment","name":"load"},"minReplicas":1,"maxReplicas":3,
       "behavior":{"scaleDown":{"stabilizationWindowSeconds":15}},"metrics":[{"type":"Resource","resource":{"name":"memory",
         "target":{"type":"Utilization","averageUtilization":80}}}]}})
    pods=c.k.items("pods",c.ns)
    c.k.call("exec","-n",c.ns,pods[0]["metadata"]["name"],"--","touch","/tmp/enable_pressure")
    c.wait(lambda:c.k.get("deployment","load",c.ns)["spec"]["replicas"]>1,"native HPA expanded")
    if shrink:
        for p in c.k.items("pods",c.ns):
            if p.get("status",{}).get("phase")=="Running":
                c.k.call("exec","-n",c.ns,p["metadata"]["name"],"--","rm","-f","/tmp/enable_pressure")
        c.wait(lambda:c.k.get("deployment","load",c.ns)["spec"]["replicas"]==1,"native HPA shrank")
    c.record("HPA final state",hpa=c.k.get("hpa","load",c.ns).get("status",{}))

def pool(c):
    s={"backend":"kubernetes","image":c.image,"command":["sleep","86400"],"replicas":1,
       "nodeSelector":c.target(),"requests":{"cpu":"50m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}}
    claim=c.k.create({"apiVersion":"platform.riscv.io/v1alpha1","kind":"ResourceClaim","metadata":{"name":"pool","namespace":c.ns},"spec":s})
    c.wait(lambda:c.k.get("resourceclaim","pool",c.ns).get("status",{}).get("phase")=="Ready","claim allocated")
    old=c.k.items("pods",c.ns)[0]["metadata"]["uid"]
    claim=c.k.get("resourceclaim","pool",c.ns); s["limits"]["cpu"]="200m"
    c.k.patch("resourceclaim",claim,{"spec":s})
    def changed():
        pods=c.k.items("pods",c.ns)
        return any(p["metadata"]["uid"]!=old and p["spec"]["containers"][0]["resources"]["limits"]["cpu"]=="200m"
          and any(x["type"]=="Ready" and x["status"]=="True" for x in p.get("status",{}).get("conditions",[])) for p in pods)
    c.wait(changed,"resource resize rolled out and became Ready")

def main():
    name=sys.argv[1]; c=Case(name)
    cases={"hpa-expand":lambda:hpa(c,False),"hpa-shrink":lambda:hpa(c,True),"affinity":lambda:affinity(c),
     "node-exclusion":lambda:exclusion(c),"preemption":lambda:preemption(c),"taint-reject":lambda:taint(c,False),
     "taint-tolerate":lambda:taint(c,True),"node-affinity":lambda:node_affinity(c),"pod-antiaffinity":lambda:anti_affinity(c),
     "resource-pool":lambda:pool(c)}
    error=None
    try: cases[name]()
    except Exception as e: error=e
    finally: c.finish(error)
    if error: raise error
if __name__=="__main__": main()

