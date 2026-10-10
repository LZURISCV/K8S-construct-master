"""HostNetwork TCP workloads: node/port accounting through the Kubernetes scheduler."""
import copy
import json
from common import OWNER, FINALIZER, owner, owned
from resource import resources

def ports(spec):
    result=[]; seen=set()
    for item in spec.get("ports",[]):
        port=item.get("containerPort")
        if isinstance(port,bool) or not isinstance(port,int) or not 1024<=port<=65535:
            raise ValueError("TCP listener port must be an integer in 1024..65535")
        if item.get("protocol","TCP")!="TCP":
            raise ValueError("this backend supports TCP only")
        if item.get("hostPort",port)!=port or item.get("hostIP","0.0.0.0")!="0.0.0.0":
            raise ValueError("host port must equal container port and cover all IPv4 addresses")
        if port in seen: raise ValueError("duplicate TCP listener port")
        seen.add(port)
        value={"containerPort":port,"hostPort":port,"hostIP":"0.0.0.0","protocol":"TCP"}
        if "name" in item: value["name"]=item["name"]
        result.append(value)
    return result

def node_ready(node):
    return not node["spec"].get("unschedulable") and any(
        c["type"]=="Ready" and c["status"]=="True" for c in node.get("status",{}).get("conditions",[]))

def workload_pod(obj):
    s=obj["spec"]; meta=obj["metadata"]
    if any(k in s for k in ("device","mac","address","gateway")):
        raise ValueError("this software backend takes node/image/ports, not NIC or IP allocation")
    if not s.get("node") or not s.get("image"): raise ValueError("node and image are required")
    container={"name":"workload","image":s["image"],"resources":resources(s),"ports":ports(s)}
    for key in ("command","args","env"):
        if key in s: container[key]=copy.deepcopy(s[key])
    spec={"containers":[container],"restartPolicy":"Never","automountServiceAccountToken":False,
          "hostNetwork":True,"dnsPolicy":"ClusterFirstWithHostNet",
          "affinity":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{"nodeSelectorTerms":[
              {"matchFields":[{"key":"metadata.name","operator":"In","values":[s["node"]]}]}]}}}}
    if "tolerations" in s: spec["tolerations"]=copy.deepcopy(s["tolerations"])
    if "imagePullSecrets" in s: spec["imagePullSecrets"]=copy.deepcopy(s["imagePullSecrets"])
    return {"apiVersion":"v1","kind":"Pod",
      "metadata":{"name":"hostnet-"+meta["uid"][:16],"namespace":meta["namespace"],"labels":{OWNER:meta["uid"]},
        "ownerReferences":owner(obj),"annotations":{"sidecar.istio.io/inject":"false",
           "platform.riscv.io/spec":json.dumps(s,sort_keys=True)}},"spec":spec}

class Networks:
    def __init__(self,kube,config=None): self.k=kube
    def step(self,obj):
        meta=obj["metadata"]; ns=meta["namespace"]
        name="hostnet-"+meta["uid"][:16]
        pod=self.k.get("pod",name,ns,missing=True)
        if meta.get("deletionTimestamp"):
            if pod:
                if not owned(pod,obj): raise ValueError("refuse deletion of unrelated Pod")
                if not pod["metadata"].get("deletionTimestamp"): self.k.delete("pod",pod)
                self.k.status(obj,{"phase":"Releasing","message":"waiting for Pod deletion and TCP port release"})
                return
            node=self.k.get("node",obj["spec"]["node"],missing=True)
            if not node or not any(c["type"]=="Ready" and c["status"]=="True" for c in node.get("status",{}).get("conditions",[])):
                self.k.status(obj,{"phase":"Releasing","message":"node not Ready; retain reservation until node recovery"})
                return
            self.k.patch("hostnetworkworkload",obj,{"metadata":{**meta,
                "finalizers":[f for f in meta.get("finalizers",[]) if f!=FINALIZER]}})
            return
        desired=workload_pod(obj)
        if pod:
            if not owned(pod,obj): raise ValueError("Pod ownership changed")
            if pod["metadata"].get("annotations",{}).get("platform.riscv.io/spec")!=json.dumps(obj["spec"],sort_keys=True):
                raise ValueError("allocated spec is immutable: delete and recreate HostNetworkWorkload")
            ready=any(c["type"]=="Ready" and c["status"]=="True" for c in pod.get("status",{}).get("conditions",[]))
            self.k.status(obj,{"phase":pod.get("status",{}).get("phase","Pending"),"ready":ready,
              "pod":name,"node":obj["spec"]["node"],"nodeIP":pod.get("status",{}).get("podIP"),
              "ports":ports(obj["spec"]),"mode":"hostNetwork-TCP"})
            return
        if FINALIZER not in meta.get("finalizers",[]):
            self.k.patch("hostnetworkworkload",obj,{"metadata":{**meta,
                "finalizers":meta.get("finalizers",[])+[FINALIZER]}})
            return
        node=self.k.get("node",obj["spec"]["node"])
        if not node_ready(node): raise ValueError("target node not Ready/schedulable")
        wanted={p["hostPort"] for p in ports(obj["spec"])}
        for peer in self.k.items("hostnetworkworkloads"):
            if peer["metadata"]["uid"]==meta["uid"] or peer["spec"]["node"]!=obj["spec"]["node"]: continue
            if wanted & {p["hostPort"] for p in ports(peer["spec"])}:
                raise ValueError("TCP port reserved by "+peer["metadata"]["namespace"]+"/"+peer["metadata"]["name"])
        self.k.create(desired)
        self.k.status(obj,{"phase":"Allocating","pod":name,"node":obj["spec"]["node"],
                          "ports":ports(obj["spec"]),"mode":"hostNetwork-TCP"})
