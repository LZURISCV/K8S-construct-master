#!/usr/bin/env python3
"""Paired cross-node Pod-TCP / hostNetwork-TCP tests with managed host workloads."""
import copy
import ipaddress
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"control"))
from common import Kube, API
from network import node_ready

LIMITS={"requests":{"cpu":"500m","memory":"64Mi"},"limits":{"cpu":"1","memory":"128Mi"}}

def host_claim(name,ns,node,image,server,port):
    return {"apiVersion":API,"kind":"HostNetworkWorkload","metadata":{"name":name,"namespace":ns},
      "spec":{"node":node,"image":image,**copy.deepcopy(LIMITS),
              "command":["iperf3","-s","-p",str(port)] if server else ["sleep","86400"],
              "ports":[{"containerPort":port}] if server else []}}

def pod_workload(name,ns,node,image,server,port):
    return {"apiVersion":"v1","kind":"Pod","metadata":{"name":name,"namespace":ns,
      "annotations":{"sidecar.istio.io/inject":"false"}},
      "spec":{"restartPolicy":"Never","automountServiceAccountToken":False,
        "affinity":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{"nodeSelectorTerms":[
          {"matchFields":[{"key":"metadata.name","operator":"In","values":[node]}]}]}}},
        "containers":[{"name":"workload","image":image,"resources":copy.deepcopy(LIMITS),
          "command":["iperf3","-s","-p",str(port)] if server else ["sleep","86400"]}]}}

def validate_endpoints(client,server,mode,client_node,server_node):
    if client_node==server_node: raise ValueError("network comparison requires two distinct Worker nodes")
    for pod,expected in ((client,client_node),(server,server_node)):
        if pod["spec"].get("nodeName")!=expected: raise ValueError("unexpected physical node placement")
        if bool(pod["spec"].get("hostNetwork",False))!=(mode=="host"):
            raise ValueError("actual Pod network mode does not match test mode")
        if not any(c["type"]=="Ready" and c["status"]=="True" for c in pod.get("status",{}).get("conditions",[])):
            raise ValueError("endpoint is not Ready")
    address=server["status"]["podIP"]
    if ipaddress.ip_address(address).version!=4: raise ValueError("this simple comparison currently requires IPv4")
    if mode=="host" and any(p["status"].get("podIP")!=p["status"].get("hostIP") for p in (client,server)):
        raise ValueError("hostNetwork endpoint IP must equal hostIP")
    return address

def wait_pod(k,ns,name,deadline):
    while time.time()<deadline:
        pod=k.get("pod",name,ns,missing=True)
        if pod and pod.get("status",{}).get("phase")=="Failed":
            raise RuntimeError(json.dumps(pod["status"]))
        if pod and any(c["type"]=="Ready" and c["status"]=="True" for c in pod.get("status",{}).get("conditions",[])):
            return pod
        time.sleep(2)
    raise TimeoutError("Pod not Ready: "+name)

def wait_claim(k,ns,name,deadline):
    while time.time()<deadline:
        obj=k.get("hostnetworkworkload",name,ns)
        status=obj.get("status",{})
        if status.get("phase")=="Error": raise RuntimeError(status.get("message","host workload failed"))
        if status.get("pod"): return wait_pod(k,ns,status["pod"],deadline)
        time.sleep(2)
    raise TimeoutError("HostNetworkWorkload not allocated: "+name)

def exec_pod(k,ns,pod,*args):
    return subprocess.check_output(k.base+["exec","-n",ns,pod,"--",*args],text=True,timeout=150)

def main():
    k=Kube(os.environ.get("KUBECONFIG"))
    image=os.environ["TEST_IMAGE"]
    client_node=os.environ["NETWORK_CLIENT_NODE"]; server_node=os.environ["NETWORK_SERVER_NODE"]
    if client_node==server_node: raise ValueError("set two distinct real Worker node names")
    seconds=int(os.environ.get("IPERF_SECONDS","30")); streams=int(os.environ.get("IPERF_STREAMS","4"))
    repeats=int(os.environ.get("NETWORK_REPEATS","3")); port=int(os.environ.get("NETWORK_PORT","25201"))
    if not 1<=seconds<=120 or not 1<=streams<=128 or repeats<1 or not 1024<=port<=65535:
        raise ValueError("invalid duration/streams/repeats/port")
    for name in (client_node,server_node):
        node=k.get("node",name)
        if not node_ready(node) or node.get("status",{}).get("nodeInfo",{}).get("architecture")!="riscv64":
            raise ValueError("test node must be Ready, schedulable and riscv64: "+name)
    directory=pathlib.Path(os.environ.get("TEST_RESULTS","./platform-test-results")); directory.mkdir(parents=True,exist_ok=True)
    comparison="rv-net-"+uuid.uuid4().hex[:10]; ns=comparison
    k.create({"apiVersion":"v1","kind":"Namespace","metadata":{"name":ns,
      "labels":{"pod-security.kubernetes.io/enforce":"privileged"}}})
    try:
        for name,node,server in (("pod-client",client_node,False),("pod-server",server_node,True)):
            k.create(pod_workload(name,ns,node,image,server,port))
        for name,node,server in (("host-client",client_node,False),("host-server",server_node,True)):
            k.create(host_claim(name,ns,node,image,server,port))
        deadline=time.time()+int(os.environ.get("TEST_TIMEOUT","600"))
        endpoints={"pod":(wait_pod(k,ns,"pod-client",deadline),wait_pod(k,ns,"pod-server",deadline)),
                   "host":(wait_claim(k,ns,"host-client",deadline),wait_claim(k,ns,"host-server",deadline))}
        addresses={mode:validate_endpoints(*pair,mode,client_node,server_node) for mode,pair in endpoints.items()}
        image_ids=set()
        for pair in endpoints.values():
            for pod in pair:
                value=pod["status"].get("containerStatuses",[{}])[0].get("imageID")
                if not value: raise ValueError("missing runtime imageID")
                image_ids.add(value)
        if len(image_ids)!=1: raise ValueError("all four endpoints must use the same actual image digest")
        routes={}
        for mode,(client,server) in endpoints.items():
            route=json.loads(exec_pod(k,ns,client["metadata"]["name"],"ip","-j","route","get",addresses[mode]))
            if not route: raise ValueError("missing route evidence")
            if mode=="host" and route[0].get("dev","").startswith(("flannel","cni","veth")):
                raise ValueError("host path unexpectedly uses an overlay/container interface")
            routes[mode]=route
            # Wait for the actual server listener; Pod readiness alone is insufficient.
            code="import socket; s=socket.create_connection(("+repr(addresses[mode])+","+str(port)+"),timeout=2); s.close()"
            for attempt in range(20):
                try: exec_pod(k,ns,client["metadata"]["name"],"python3","-c",code); break
                except subprocess.CalledProcessError:
                    if attempt==19: raise
                    time.sleep(1)
        records=[]
        for trial in range(repeats):
            for mode in (("pod","host") if trial%2==0 else ("host","pod")):
                client,server=endpoints[mode]
                raw=json.loads(exec_pod(k,ns,client["metadata"]["name"],"iperf3","-c",addresses[mode],"-p",str(port),
                    "-t",str(seconds),"-P",str(streams),"-J"))
                if raw.get("error"): raise RuntimeError(raw["error"])
                rate=float(raw["end"]["sum_received"]["bits_per_second"])
                if not rate>0: raise ValueError("nonpositive throughput")
                record={"operation":"iperf3","mode":mode,"comparisonId":comparison,"trial":trial,
                  "seconds":seconds,"streams":streams,"port":port,"resources":LIMITS,"image":image,"imageID":next(iter(image_ids)),
                  "clientPodUID":client["metadata"]["uid"],"serverPodUID":server["metadata"]["uid"],
                  "clientNode":client_node,"serverNode":server_node,"clientIP":client["status"]["podIP"],"serverIP":addresses[mode],
                  "hostNetwork":mode=="host","route":routes[mode],"raw":raw,"bitsPerSecond":rate,"evidence":"real-iperf3"}
                (directory/(comparison+"-"+str(trial)+"-"+mode+".json")).write_text(json.dumps(record,indent=2),encoding="utf-8")
                records.append(record)
        means={mode:sum(r["bitsPerSecond"] for r in records if r["mode"]==mode)/repeats for mode in ("pod","host")}
        print(json.dumps({"comparisonId":comparison,"meansBitsPerSecond":means,"hostOverPod":means["host"]/means["pod"]},indent=2))
    finally:
        if os.environ.get("KEEP_TEST_NAMESPACE")!="1":
            k.call("delete","namespace",ns,"--wait=true","--timeout=60s","-o","json")
if __name__=="__main__": main()
