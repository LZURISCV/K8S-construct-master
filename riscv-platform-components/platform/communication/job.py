#!/usr/bin/env python3
"""MPIJob v2beta1 using ordinary Ethernet/TCP; host networking is the default."""
import argparse
import ipaddress
import json

def job(name,namespace,image,op,mode,workers=2,slots=2,count=65536,repeats=7,chunk=4096,work=32,root=0,
        limited=False,spread=False,mpi_args=None,network="host",tcp_network=None,ssh_port=25222):
    if not 1<=workers<=10000 or not 1<=slots<=1024: raise ValueError("invalid workers/slots")
    if network not in ("host","pod"): raise ValueError("network must be host or pod")
    if not 1024<=ssh_port<=65535: raise ValueError("SSH port must be 1024..65535")
    if network=="host" and not tcp_network: raise ValueError("host MPI requires tcp_network: common reachable physical IPv4 subnet")
    if tcp_network:
        subnet=ipaddress.ip_network(tcp_network,strict=True)
        if subnet.version!=4: raise ValueError("this minimal MPI setup uses IPv4")
    extra=mpi_args or []
    if not isinstance(extra,list) or any(not isinstance(v,str) for v in extra): raise ValueError("mpi_args must be string array")
    reserved={"pml","btl","btl_tcp_if_include","btl_tcp_if_exclude","plm_rsh_agent","plm_rsh_args"}
    if any(arg in reserved or any(arg.startswith("--"+key+"=") for key in reserved) for arg in extra):
        raise ValueError("MPI_ARGS may tune collectives but cannot override TCP/SSH transport")
    args=["python3","/opt/rv-communication/launch.py","--allow-run-as-root","--bind-to","none",
          "--hostfile","/etc/mpi/hostfile","-np",str(workers*slots),
          "--mca","plm_rsh_args","-p "+str(ssh_port)+" -o ConnectionAttempts=10"]
    if tcp_network: args += ["--mca","btl_tcp_if_include",tcp_network]
    args += extra+["rv-bench",op,mode,str(count),str(repeats),str(chunk),str(work),str(root)]
    env=[]
    if tcp_network:
        env=[{"name":"OMPI_MCA_oob_tcp_if_include","value":tcp_network},
             {"name":"PRTE_MCA_oob_tcp_if_include","value":tcp_network}]
    worker_container={"name":"mpi","image":image,
      "command":["/bin/sh","-ec",'exec /usr/sbin/sshd -D -e -p "$RV_MPI_SSH_PORT" -o "SetEnv=RV_WORKER_POD=$RV_WORKER_POD"'],
      "env":[{"name":"RV_MPI_SSH_PORT","value":str(ssh_port)},
             {"name":"RV_WORKER_POD","valueFrom":{"fieldRef":{"fieldPath":"metadata.name"}}},*env],
      "ports":[{"name":"mpi-ssh","containerPort":ssh_port,"protocol":"TCP"}],
      "readinessProbe":{"tcpSocket":{"port":ssh_port},"initialDelaySeconds":1,"periodSeconds":2},
      "resources":{"requests":{"cpu":"500m","memory":"256Mi"},"limits":{"cpu":"1" if limited else str(max(2,slots)),"memory":"1Gi"}}}
    worker={"containers":[worker_container]}
    launcher={"containers":[{"name":"mpi","image":image,"command":args,"env":env,
      "resources":{"requests":{"cpu":"100m","memory":"64Mi"},"limits":{"cpu":"1","memory":"256Mi"}}}]}
    if network=="host":
        for pod in (worker,launcher):
            pod.update(hostNetwork=True,dnsPolicy="ClusterFirstWithHostNet")
        worker_container["ports"][0]["hostPort"]=ssh_port
        spread=True
    if spread:
        worker["affinity"]={"podAntiAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":[
          {"labelSelector":{"matchLabels":{"platform.riscv.io/mpi-job":name,"platform.riscv.io/mpi-role":"worker"}},
           "topologyKey":"kubernetes.io/hostname"}]}}
    return {"apiVersion":"kubeflow.org/v2beta1","kind":"MPIJob","metadata":{"name":name,"namespace":namespace},
       "spec":{"slotsPerWorker":slots,"launcherCreationPolicy":"WaitForWorkersReady","runPolicy":{"cleanPodPolicy":"None"},
        "mpiReplicaSpecs":{"Launcher":{"replicas":1,"restartPolicy":"Never","template":{
          "metadata":{"annotations":{"sidecar.istio.io/inject":"false"}},"spec":launcher}},
        "Worker":{"replicas":workers,"restartPolicy":"Never","template":{
          "metadata":{"annotations":{"sidecar.istio.io/inject":"false"},
            "labels":{"platform.riscv.io/mpi-job":name,"platform.riscv.io/mpi-role":"worker"}},"spec":worker}}}}}
def main():
    p=argparse.ArgumentParser()
    for key in ("image","operation","mode"): p.add_argument("--"+key,required=True)
    p.add_argument("--name",default="rv-comm");p.add_argument("--namespace",default="default")
    for key,default in [("workers",2),("slots",2),("count",65536),("repeats",7),("chunk",4096),("work",32),("root",0),("ssh-port",25222)]:
        p.add_argument("--"+key,type=int,default=default)
    p.add_argument("--network",choices=["host","pod"],default="host");p.add_argument("--tcp-network")
    p.add_argument("--limited",action="store_true");p.add_argument("--spread",action="store_true")
    a=p.parse_args()
    print(json.dumps(job(a.name,a.namespace,a.image,a.operation,a.mode,a.workers,a.slots,a.count,a.repeats,a.chunk,a.work,a.root,
        a.limited,a.spread,network=a.network,tcp_network=a.tcp_network,ssh_port=a.ssh_port),indent=2))
if __name__=="__main__": main()
