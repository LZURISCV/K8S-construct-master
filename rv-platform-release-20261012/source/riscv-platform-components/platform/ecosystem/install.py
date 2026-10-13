#!/usr/bin/env python3
"""Helm deployment with registry auditing or explicitly selected offline images."""
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

def run(*args,data=None):
    return subprocess.check_output(list(args),input=data,text=True)

def audit(image):
    result=json.loads(run("skopeo","inspect","--override-os","linux","--override-arch","riscv64","docker://"+image))
    if result.get("Architecture")!="riscv64" or result.get("Os","linux")!="linux":
        raise ValueError("image does not contain linux/riscv64: "+image)
    return {"image":image,"digest":result["Digest"],"architecture":result["Architecture"]}

def deployment_values(config, offline=False):
    values={key:config[key] for key in ("prometheusImage","nodeTargets")}
    for key in ("prometheusImage",):
        if not isinstance(values[key],str) or not values[key].strip():
            raise ValueError("set "+key)
    if not isinstance(values["nodeTargets"],list) or not values["nodeTargets"]:
        raise ValueError("nodeTargets must contain at least one exporter address")
    if any(not isinstance(target,str) or not target.strip() for target in values["nodeTargets"]):
        raise ValueError("nodeTargets entries must be nonempty strings")
    for key in ("storage","nodeSelector","service","retention"):
        if key in config: values[key]=config[key]
    values["imagePullPolicy"]="Never" if offline else config.get("imagePullPolicy","IfNotPresent")
    if offline and not values.get("nodeSelector"):
        raise ValueError("offline deployment requires nodeSelector for nodes with imported images")
    return values

def check_offline_nodes(values):
    selector=",".join(key+"="+value for key,value in values["nodeSelector"].items())
    nodes=json.loads(run("kubectl","get","nodes","-l",selector,"-o","json"))["items"]
    if not nodes: raise ValueError("nodeSelector does not match any nodes: "+selector)
    for node in nodes:
        info=node["status"].get("nodeInfo",{})
        if info.get("architecture")!="riscv64" or info.get("operatingSystem")!="linux":
            raise ValueError("selected node is not linux/riscv64: "+node["metadata"]["name"])
    if not any(any(c["type"]=="Ready" and c["status"]=="True"
                   for c in node["status"].get("conditions",[])) for node in nodes):
        raise ValueError("no selected node is Ready")
    # Kubelet image inventory can lag. Never is the final gate: absent images
    # fail visibly with ErrImageNeverPull instead of contacting a registry.

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config",help="monitoring configuration JSON")
    parser.add_argument("--offline",action="store_true",
                        help="use imported node images, force Never, skip remote registry access")
    args=parser.parse_args()
    config=json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    values=deployment_values(config,args.offline)
    directory=pathlib.Path(__file__).resolve().parent
    ns=config.get("namespace","rv-monitoring")
    if args.offline:
        check_offline_nodes(values)
        images=[{"image":values[k],"mode":"offline","pullPolicy":"Never"}
                for k in ("prometheusImage",)]
    else:
        images=[audit(values["prometheusImage"])]
    run("kubectl","create","namespace",ns,"--dry-run=client","-o","json") # validated below via apply
    namespace={"apiVersion":"v1","kind":"Namespace","metadata":{"name":ns}}
    run("kubectl","apply","-f","-",data=json.dumps(namespace))
    with tempfile.TemporaryDirectory(prefix="rv-helm-") as temp:
        path=pathlib.Path(temp,"values.json"); path.write_text(json.dumps(values),encoding="utf-8")
        print("Validating local Helm chart...",flush=True)
        print(run("helm","lint","--strict",str(directory/"chart"),"-f",str(path)),end="")
        print("Deploying Prometheus; waiting for readiness (up to 10 minutes)...",flush=True)
        print(run("helm","upgrade","--install","rv-observability",str(directory/"chart"),"-n",ns,"-f",str(path),"--wait","--timeout","10m"),end="")
    run("kubectl","rollout","status","deployment/rv-prometheus","-n",ns,"--timeout=300s")
    print(json.dumps({"installedImages":images,"namespace":ns},indent=2))
if __name__=="__main__": main()

