"""Resource HAL: Kubernetes workload backend + node configuration backend."""
import copy
import json
from common import API, OWNER, PREFIX, FINALIZER, now, owned, owner, quantity

def resources(spec):
    requests=spec.get("requests",{})
    limits=spec.get("limits",{})
    if not requests or not limits:
        raise ValueError("both requests and limits are required")
    for key in set(requests)|set(limits):
        if key not in requests or key not in limits:
            raise ValueError("each resource needs request and limit")
        low,high=quantity(requests[key]),quantity(limits[key])
        if low<=0 or high<low:
            raise ValueError("invalid request/limit for "+key)
        # CPU/memory and existing vendor device-plugin extended resources.
        if key not in ("cpu","memory","ephemeral-storage"):
            if "/" not in key or low!=high or low!=int(low):
                raise ValueError("extended resource needs integer equal request/limit")
    return {"requests":requests,"limits":limits}

class KubernetesBackend:
    """Describe/Allocate/Resize/Release use normal scheduler accounting.
    Resize rolls out new Pods; never silently changes cgroups behind kubelet.
    """
    def __init__(self,kube):
        self.k=kube
    def describe(self, claim):
        return self.k.get("deployment",claim["metadata"]["name"],claim["metadata"]["namespace"],missing=True)
    def desired(self,claim):
        s=claim["spec"]; ns=claim["metadata"]["namespace"]
        if s.get("backend","kubernetes")!="kubernetes":
            raise ValueError("backend must be kubernetes; devices use registered extended resources")
        r=resources(s)
        replicas=int(s.get("replicas",1))
        if not 0<=replicas<=int(s.get("maxReplicas",10000)):
            raise ValueError("replicas outside configured bounds")
        c={"name":"workload","image":s["image"],"resources":r}
        for key in ("command","args","env","ports"):
            if key in s: c[key]=copy.deepcopy(s[key])
        pod={"containers":[c],"automountServiceAccountToken":False}
        for key in ("nodeSelector","affinity","tolerations","runtimeClassName","imagePullSecrets"):
            if key in s: pod[key]=copy.deepcopy(s[key])
        pod["securityContext"]={"seccompProfile":{"type":"RuntimeDefault"}}
        if "nodeName" in s: raise ValueError("use nodeSelector; allocation must pass scheduler")
        labels={OWNER:claim["metadata"]["uid"]}
        return {"apiVersion":"apps/v1","kind":"Deployment",
          "metadata":{"name":claim["metadata"]["name"],"namespace":ns,"labels":labels,"ownerReferences":owner(claim)},
          "spec":{"replicas":replicas,"selector":{"matchLabels":labels},"template":{
              "metadata":{"labels":labels,"annotations":{"sidecar.istio.io/inject":"false"}},
              "spec":pod}}}
    def allocate(self,claim):
        desired=self.desired(claim); current=self.describe(claim)
        if not current: return self.k.create(desired)
        if not owned(current,claim): raise ValueError("deployment name belongs to another owner")
        # HPA/policies own replicas when explicitly selected, not this backend.
        replicas=desired["spec"]["replicas"]
        if claim["spec"].get("externalScaling",False):
            replicas=current["spec"]["replicas"]
        patch={"replicas":replicas,"template":desired["spec"]["template"]}
        if all(current["spec"].get(k)==v for k,v in patch.items()): return current
        return self.k.patch("deployment",current,{"spec":{**current["spec"],**patch}})
    resize=allocate
    def release(self,claim):
        # K8s ownerReferences perform UID-bound garbage collection.
        return {"method":"ownerReference","uid":claim["metadata"]["uid"]}

class Resources:
    def __init__(self,kube):
        self.k=kube; self.backend=KubernetesBackend(kube)
    def claim(self,obj):
        current=self.backend.allocate(obj)
        st=current.get("status",{}); desired=current["spec"]["replicas"]
        ready=(st.get("observedGeneration",0)>=current["metadata"].get("generation",1)
               and st.get("updatedReplicas",0)>=desired and st.get("availableReplicas",0)>=desired)
        self.k.status(obj,{"phase":"Ready" if ready else "Allocating",
            "workload":current["metadata"]["name"],"workloadUID":current["metadata"]["uid"],
            "replicas":current["spec"]["replicas"],"availableReplicas":current.get("status",{}).get("availableReplicas",0),
            "backend":"kubernetes","resizeMode":"RollingUpdate"})
    def profile(self,obj):
        s=obj["spec"]; node=self.k.get("node",s["node"])
        labels=s.get("labels",{})
        if any(not k.startswith(PREFIX) for k in labels):
            raise ValueError("NodeProfile manages only platform.riscv.io/ labels")
        previous=obj.get("status",{}).get("managedLabels",[])
        fresh={k:v for k,v in node["metadata"].get("labels",{}).items() if k not in previous}
        fresh.update(labels)
        taints=s.get("taints",[])
        if any(not t["key"].startswith(PREFIX) or t["effect"] not in ("NoSchedule","PreferNoSchedule") for t in taints):
            raise ValueError("only own NoSchedule/PreferNoSchedule taints are allowed")
        others=[t for t in node["spec"].get("taints",[]) if not t["key"].startswith(PREFIX)]
        # Exclusive policy per node avoids competing profile writers.
        peers=self.k.items("nodeprofiles")
        if any(p["metadata"]["uid"]!=obj["metadata"]["uid"] and p["spec"]["node"]==s["node"] for p in peers):
            raise ValueError("another NodeProfile owns this node")
        meta={**node["metadata"],"labels":fresh}
        spec={**node["spec"],"taints":others+taints}
        if "schedulable" in s: spec["unschedulable"]=not s["schedulable"]
        if meta!=node["metadata"] or spec!=node["spec"]:
            self.k.patch("node",node,{"metadata":meta,"spec":spec})
        self.k.status(obj,{"phase":"Ready","node":s["node"],"managedLabels":list(labels),
          "allocatable":node.get("status",{}).get("allocatable",{}),
          "message":"node placement configuration applied; deleting profile does not undo node configuration"})

