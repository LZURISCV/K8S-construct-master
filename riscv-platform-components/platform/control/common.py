"""Small Kubernetes client shared by platform controllers (stdlib + kubectl)."""
import copy
import json
import math
import re
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal

GROUP = "platform.riscv.io"
API = GROUP + "/v1alpha1"
OWNER = GROUP + "/owner"
FINALIZER = GROUP + "/cleanup"
PREFIX = GROUP + "/"
RESOURCES={"resourceclaim":"resourceclaims","resourcepolicy":"resourcepolicies","nodeprofile":"nodeprofiles",
           "hostnetworkworkload":"hostnetworkworkloads","scenariopolicy":"scenariopolicies"}

def qualified(kind):
    if kind in RESOURCES: return RESOURCES[kind]+"."+GROUP
    if kind in RESOURCES.values(): return kind+"."+GROUP
    return kind

def now():
    return datetime.now(timezone.utc).isoformat()

def quantity(v):
    m = re.fullmatch(r"((?:\d+(?:\.\d*)?|\.\d+))([a-zA-Z]*)", str(v))
    factors = {"":1, "m":Decimal(".001"), "n":Decimal("1e-9"), "u":Decimal("1e-6")}
    factors.update({s:1024**i for i,s in enumerate(["","Ki","Mi","Gi","Ti","Pi","Ei"]) if s})
    factors.update({s:1000**i for i,s in enumerate(["","k","M","G","T","P","E"]) if s})
    if not m or m[2] not in factors:
        raise ValueError("invalid nonnegative quantity: " + str(v))
    return Decimal(m[1]) * factors[m[2]]

def bounded(value, lower, upper):
    if not math.isfinite(float(value)):
        raise ValueError("nonfinite value")
    return max(lower, min(upper, value))

def owned(obj, parent):
    return obj.get("metadata", {}).get("labels", {}).get(OWNER) == parent["metadata"]["uid"]

def owner(parent):
    return [{"apiVersion":parent["apiVersion"], "kind":parent["kind"],
             "name":parent["metadata"]["name"], "uid":parent["metadata"]["uid"],
             "controller":True, "blockOwnerDeletion":True}]

class Kube:
    def __init__(self, config=None):
        self.base = ["kubectl"] + (["--kubeconfig", config] if config else [])
    def call(self, *args, data=None, missing=False):
        p = subprocess.run(self.base+list(args), input=json.dumps(data) if data is not None else None,
                           text=True, capture_output=True, timeout=90)
        if p.returncode:
            if missing and ("NotFound" in p.stderr or "not found" in p.stderr):
                return None
            raise RuntimeError(p.stderr.strip()[:3000])
        return json.loads(p.stdout) if p.stdout.strip() else {}
    def get(self, kind, name, ns=None, missing=False):
        return self.call("get",qualified(kind),name,*(["-n",ns] if ns else []),"-o","json",missing=missing)
    def items(self, kind, ns=None):
        return self.call("get",qualified(kind),*(["-n",ns] if ns else ["-A"]),"-o","json")["items"]
    def create(self, obj):
        return self.call("create","-f","-","-o","json",data=obj)
    def patch(self, kind, obj, patch):
        # Optimistic concurrency: do not overwrite another writer's decisions.
        ops=[{"op":"test","path":"/metadata/uid","value":obj["metadata"]["uid"]},
             {"op":"test","path":"/metadata/resourceVersion","value":obj["metadata"]["resourceVersion"]}]
        ops.extend({"op":"add","path":"/"+key,"value":value} for key,value in patch.items())
        return self.call("patch",qualified(kind),obj["metadata"]["name"],
                         *(["-n",obj["metadata"]["namespace"]] if obj["metadata"].get("namespace") else []),
                         "--type=json","-p",json.dumps(ops),"-o","json")
    def status(self, obj, status):
        plural={"ResourcePolicy":"resourcepolicies","ResourceClaim":"resourceclaims",
                "NodeProfile":"nodeprofiles","HostNetworkWorkload":"hostnetworkworkloads","ScenarioPolicy":"scenariopolicies"}[obj["kind"]]
        meta=obj["metadata"]
        path=f"/apis/{API}/namespaces/{meta['namespace']}/{plural}/{meta['name']}"
        fresh=self.call("get","--raw",path)
        fresh["status"]={**status,"observedGeneration":meta.get("generation",1),"updatedAt":now()}
        return self.call("replace","--raw",path+"/status","-f","-",data=fresh)
    def delete(self, kind, obj):
        meta=obj["metadata"]
        if kind=="pod":
            path=f"/api/v1/namespaces/{meta['namespace']}/pods/{meta['name']}"
        else:
            raise ValueError("unsupported deletion type")
        return self.call("delete","--raw",path,"-f","-",data={"apiVersion":"v1","kind":"DeleteOptions",
                         "preconditions":{"uid":meta["uid"]}})

def metric_sample(vector, timestamp=None, max_age=45):
    """Reject missing, multiple, stale, future or nonfinite Prometheus series."""
    timestamp = time.time() if timestamp is None else timestamp
    if len(vector)!=1:
        raise ValueError("query must return exactly one aggregated series")
    stamp,value=vector[0]["value"]
    if timestamp-float(stamp)>max_age or float(stamp)>timestamp+5:
        raise ValueError("stale/future metric")
    value=float(value)
    if not math.isfinite(value):
        raise ValueError("nonfinite metric")
    return value

def scale_decision(current, sample, target, minimum, maximum, tolerance=.1):
    if not 0<target or not 1<=minimum<=maximum:
        raise ValueError("invalid scale bounds/target")
    if abs(sample/target-1)<=tolerance:
        return current
    return int(bounded(math.ceil(current*sample/target),minimum,maximum))

def resume_scale(kube,obj,timestamp):
    """Replay an absolute persisted target after an interrupted scale write."""
    status=obj.get("status",{}); intent=status.get("pendingScale")
    if not intent: return False
    ns=obj["metadata"]["namespace"]
    current=kube.get("deployment",intent["workload"],ns)
    if current["metadata"]["uid"]!=intent["workloadUID"]:
        raise ValueError("scale target UID changed during recovery")
    if current["metadata"].get("annotations",{}).get(PREFIX+"allow-scaling")!=obj["metadata"]["name"]:
        raise ValueError("scale authorization revoked during recovery")
    if any(h["spec"]["scaleTargetRef"]["name"]==intent["workload"] for h in kube.items("horizontalpodautoscalers",ns)):
        raise ValueError("HPA owns pending scale target")
    if current["spec"]["replicas"]!=intent["to"]:
        kube.patch("deployment",current,{"spec":{**current["spec"],"replicas":intent["to"]}})
    kube.status(obj,{**status,"pendingScale":None,"phase":"ActionSubmitted","lastActionEpoch":timestamp,
       "lastScaleEpoch":timestamp,"consecutive":0,"replicas":intent["to"],
       "lastAction":{"type":"scale","from":intent["from"],"to":intent["to"]}})
    return True

def persist_scale(kube,obj,deployment,target,status,timestamp):
    intent={"workload":deployment["metadata"]["name"],"workloadUID":deployment["metadata"]["uid"],
            "from":deployment["spec"]["replicas"],"to":target}
    prepared={**status,"pendingScale":intent,"phase":"Scaling"}
    kube.status(obj,prepared) # write-ahead before changing the workload
    resume_scale(kube,{**obj,"status":prepared},timestamp)

