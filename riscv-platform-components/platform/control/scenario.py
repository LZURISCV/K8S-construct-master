"""Prometheus sensing, hysteresis/cooldown, bounded scaling and migration actions."""
import hashlib
import json
import time
import urllib.parse
import urllib.request
from common import API, metric_sample, now, owned, scale_decision, PREFIX, persist_scale, resume_scale

def query(url,expr):
    endpoint=url.rstrip("/")+"/api/v1/query?"+urllib.parse.urlencode({"query":expr})
    with urllib.request.urlopen(endpoint,timeout=10) as response:
        data=json.load(response)
    if data.get("status")!="success" or data["data"]["resultType"]!="vector":
        raise ValueError("Prometheus query failed or is not a vector")
    return data["data"]["result"]

def decide(spec,status,value,timestamp):
    high=float(spec["high"]); low=float(spec["low"])
    if not low<high: raise ValueError("low must be less than high")
    direction="high" if value>=high else "low" if value<=low else "hold"
    count=(int(status.get("consecutive",0))+1) if direction==status.get("direction") else 1
    if direction=="hold": count=0
    ready=(direction!="hold" and count>=int(spec.get("consecutiveSamples",3))
           and timestamp-float(status.get("lastActionEpoch",0))>=float(spec.get("cooldownSeconds",60)))
    return direction,count,ready

class Scenarios:
    def __init__(self,kube,config,reader=query,clock=time.time):
        self.k=kube; self.config=config; self.reader=reader; self.clock=clock
    def step(self,obj):
        s=obj["spec"]; st=obj.get("status",{}); timestamp=self.clock()
        if resume_scale(self.k,obj,timestamp): return
        vector=self.reader(self.config["prometheusURL"],s["query"])
        value=metric_sample(vector,timestamp,int(s.get("maxMetricAgeSeconds",45)))
        sample_time=float(vector[0]["value"][0])
        # Polling an identical scrape must not count as multiple observations.
        if sample_time<=float(st.get("lastSampleEpoch",0)): return
        direction,count,ready=decide(s,st,value,timestamp)
        result={**st,"phase":"Observing","value":value,"direction":direction,"consecutive":count,
                "lastSampleEpoch":sample_time}
        if ready:
            action=s.get(direction+"Action")
            if not action: self.k.status(obj,result); return
            if action["type"]=="scale":
                ns=obj["metadata"]["namespace"]; d=self.k.get("deployment",action["deployment"],ns)
                if d["metadata"].get("annotations",{}).get(PREFIX+"allow-scaling")!=obj["metadata"]["name"]:
                    raise ValueError("deployment must opt in to this policy by annotation")
                peers=self.k.items("horizontalpodautoscalers",ns)
                if any(h["spec"]["scaleTargetRef"]["name"]==d["metadata"]["name"] for h in peers):
                    raise ValueError("HPA already controls replicas; refuse competing writers")
                if any(p["spec"]["deployment"]==d["metadata"]["name"] for p in self.k.items("resourcepolicies",ns)):
                    raise ValueError("ResourcePolicy already controls this deployment")
                if any(p["metadata"]["uid"]!=obj["metadata"]["uid"] and
                       any(p["spec"].get(k,{}).get("deployment")==d["metadata"]["name"] for k in ("highAction","lowAction"))
                       for p in self.k.items("scenariopolicies",ns)):
                    raise ValueError("another scenario policy controls this deployment")
                current=int(d["spec"]["replicas"])
                target=int(action.get("replicas",current+int(action.get("delta",0))))
                lower=int(s.get("minReplicas",1)); upper=int(s.get("maxReplicas",100))
                target=max(lower,min(upper,target))
                persist_scale(self.k,obj,d,target,result,timestamp)
                return
            elif action["type"]=="migrate":
                ns=obj["metadata"]["namespace"]; source=self.k.get("pod",action["sourcePod"],ns)
                identity=obj["metadata"]["uid"]+source["metadata"]["uid"]+action["targetNode"]
                name="scene-"+hashlib.sha256(identity.encode()).hexdigest()[:24]
                migration=self.k.get("nodemigration",name,ns,missing=True)
                if not migration:
                    migration=self.k.create({"apiVersion":"migration.riscv.io/v1alpha1","kind":"NodeMigration",
                      "metadata":{"name":name,"namespace":ns,"labels":{PREFIX+"scenario":obj["metadata"]["name"]}},
                      "spec":{"sourcePod":source["metadata"]["name"],"targetNode":action["targetNode"]}})
                result.update(lastAction={"type":"migrate","name":name,"phase":migration.get("status",{}).get("phase","Pending")})
            elif action["type"]=="configureNode":
                ns=obj["metadata"]["namespace"]
                profile=self.k.get("nodeprofile",action["profile"],ns)
                if profile["metadata"].get("annotations",{}).get(PREFIX+"allow-scenario")!=obj["metadata"]["name"]:
                    raise ValueError("NodeProfile must opt in to this scenario")
                desired={**profile["spec"]}
                for key in ("labels","taints","schedulable"):
                    if key in action: desired[key]=action[key]
                self.k.patch("nodeprofile",profile,{"spec":desired})
                result.update(lastAction={"type":"configureNode","profile":action["profile"]})
            else: raise ValueError("unknown action type")
            result.update(lastActionEpoch=timestamp,consecutive=0,phase="ActionSubmitted")
        self.k.status(obj,result)

class Autoscaler:
    """Optional resource policy, distinct ownership from native HPA."""
    def __init__(self,kube,config,reader=query,clock=time.time):
        self.k=kube; self.config=config; self.reader=reader; self.clock=clock
    def step(self,obj):
        s=obj["spec"]; ns=obj["metadata"]["namespace"]
        if resume_scale(self.k,obj,self.clock()): return
        d=self.k.get("deployment",s["deployment"],ns)
        if d["metadata"].get("annotations",{}).get(PREFIX+"allow-scaling")!=obj["metadata"]["name"]:
            raise ValueError("deployment must opt in to this ResourcePolicy")
        if any(h["spec"]["scaleTargetRef"]["name"]==s["deployment"] for h in self.k.items("horizontalpodautoscalers",ns)):
            raise ValueError("native HPA already owns scale")
        if any(any(p["spec"].get(k,{}).get("deployment")==s["deployment"] for k in ("highAction","lowAction"))
               for p in self.k.items("scenariopolicies",ns)):
            raise ValueError("ScenarioPolicy already owns scale")
        if any(p["metadata"]["uid"]!=obj["metadata"]["uid"] and p["spec"]["deployment"]==s["deployment"]
               for p in self.k.items("resourcepolicies",ns)):
            raise ValueError("another ResourcePolicy owns scale")
        value=metric_sample(self.reader(self.config["prometheusURL"],s["query"]),self.clock())
        st=obj.get("status",{}); current=d["spec"]["replicas"]
        desired=scale_decision(current,value,float(s["target"]),int(s["minReplicas"]),int(s["maxReplicas"]))
        # Observe demand during cooldown so the two windows are not doubled.
        low_since=float(st.get("lowSinceEpoch") or self.clock()) if desired<current else None
        if desired<current and self.clock()-low_since<int(s.get("downscaleStabilizationSeconds",300)):
            desired=current
        if self.clock()-float(st.get("lastScaleEpoch",0))<int(s.get("cooldownSeconds",30)):
            desired=current
        result={**st,"phase":"Ready","value":value,"replicas":current,"desiredReplicas":desired,"lowSinceEpoch":low_since}
        if desired!=current:
            persist_scale(self.k,obj,d,desired,result,self.clock())
        else: self.k.status(obj,result)

