#!/usr/bin/env python3
import collections
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"control"))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"communication"))
from common import Kube
from job import job

def validate_ranks(sample,members,workers,slots,spread):
    pods=[p for p in members if "worker" in p["metadata"]["name"]]
    names={p["metadata"]["name"] for p in pods}
    observed=collections.Counter(sample.get("worker_pod_names",[]))
    if len(pods)!=workers or set(observed)!=names or any(n!=slots for n in observed.values()):
        raise AssertionError("MPI ranks must execute on exactly the Worker Pods with slots ranks each")
    nodes=sorted(p["spec"].get("nodeName","") for p in pods)
    if not all(nodes) or (spread and len(set(nodes))!=workers):
        raise AssertionError("Worker physical-node placement does not match requested topology")
    if sample.get("ranks")!=workers*slots:
        raise AssertionError("unexpected MPI rank count")
    return nodes

def main():
    operation=sys.argv[1]; k=Kube(os.environ.get("KUBECONFIG")); image=os.environ["MPI_IMAGE"]
    network=os.environ.get("MPI_NETWORK","host"); tcp_network=os.environ.get("MPI_TCP_NETWORK")
    ssh_port=int(os.environ.get("MPI_SSH_PORT","25222"))
    spread=network=="host" or os.environ.get("MPI_SPREAD","1")=="1"
    ns="rv-mpi-"+uuid.uuid4().hex[:10]; results=pathlib.Path(os.environ.get("TEST_RESULTS","./platform-test-results"))
    results.mkdir(parents=True,exist_ok=True)
    settings={key:int(os.environ.get("MPI_"+key.upper(),default)) for key,default in
              [("workers",2),("slots",2),("count",65536),("repeats",7),("chunk",4096),("work",32),("root",0)]}
    mpi_args=json.loads(os.environ.get("MPI_ARGS_JSON","[]"))
    modes=[("serial",False),("overlap",False),("serial",True),("overlap",True)] if operation=="fusion" else [("native",False),("hierarchical",False)]
    # Validate before creating a namespace.
    job("check",ns,image,operation,modes[0][0],**settings,network=network,tcp_network=tcp_network,ssh_port=ssh_port,mpi_args=mpi_args)
    labels={"pod-security.kubernetes.io/enforce":"privileged"} if network=="host" else {}
    k.create({"apiVersion":"v1","kind":"Namespace","metadata":{"name":ns,"labels":labels}})
    try:
        for index,(mode,limited) in enumerate(modes):
            name="bench-"+str(index); spec=job(name,ns,image,operation,mode,**settings,limited=limited,
                  spread=spread,mpi_args=mpi_args,network=network,tcp_network=tcp_network,ssh_port=ssh_port)
            k.create(spec); started=time.time()
            while True:
                obj=k.get("mpijob",name,ns); conditions=obj.get("status",{}).get("conditions",[])
                if any(c["type"]=="Failed" and c["status"]=="True" for c in conditions): raise RuntimeError(json.dumps(obj["status"]))
                if any(c["type"]=="Succeeded" and c["status"]=="True" for c in conditions): break
                if time.time()-started>int(os.environ.get("TEST_TIMEOUT","1200")): raise TimeoutError(name)
                time.sleep(3)
            members=[p for p in k.items("pods",ns) if p["metadata"].get("labels",{}).get("training.kubeflow.org/job-name")==name or p["metadata"]["name"].startswith(name+"-")]
            launchers=[p for p in members if "launcher" in p["metadata"]["name"]]
            if len(launchers)!=1: raise ValueError("cannot uniquely identify launcher")
            logs=subprocess.check_output(k.base+["logs",launchers[0]["metadata"]["name"],"-n",ns],text=True)
            (results/(ns+"-"+name+".log")).write_text(logs,encoding="utf-8")
            samples=[json.loads(line) for line in logs.splitlines() if line.startswith('{"operation"')]
            if len(samples)!=1 or not samples[0]["correct"]: raise AssertionError("missing/failed numeric result")
            sample=samples[0]
            nodes=validate_ranks(sample,members,settings["workers"],settings["slots"],spread)
            image_ids=sorted({c["imageID"] for p in members for c in p.get("status",{}).get("containerStatuses",[]) if c.get("imageID")})
            if len(image_ids)!=1: raise AssertionError("MPI Pods must use one actual image digest")
            sample.update(image=image,imageID=image_ids[0],limited=limited,mpiArgs=mpi_args,settings=settings,
                networkMode=network,transport="TCP",tcpNetwork=tcp_network,sshPort=ssh_port,evidence="real-MPIJob",
                workerNodes=nodes,serverVersion=k.call("version","-o","json").get("serverVersion",{}))
            (results/(ns+"-"+name+".json")).write_text(json.dumps(sample,indent=2),encoding="utf-8")
            print(json.dumps(sample),flush=True)
            # Release hostPort before the next comparison. Raw logs/JSON are already saved.
            k.call("delete","mpijob",name,"-n",ns,"--cascade=foreground","--wait=true","--timeout=60s","-o","json")
            deadline=time.time()+120
            while any(p["metadata"]["name"].startswith(name+"-") for p in k.items("pods",ns)):
                if time.time()>deadline: raise TimeoutError("Worker ports not released: "+name)
                time.sleep(2)
    finally:
        if os.environ.get("KEEP_TEST_NAMESPACE")!="1": k.call("delete","namespace",ns,"--wait=false","-o","json")
if __name__=="__main__": main()
