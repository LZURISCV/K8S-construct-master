#!/usr/bin/env python3
"""Architecture-audited Helm deployment; health gates are mandatory."""
import json
import pathlib
import secrets
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

def main():
    config=json.load(open(sys.argv[1],encoding="utf-8"))
    directory=pathlib.Path(__file__).resolve().parent
    ns=config.get("namespace","rv-monitoring")
    images=[audit(config[k]) for k in ("prometheusImage","grafanaImage")]
    if config.get("enableIstio",True):
        source=pathlib.Path(config["istioSource"])
        for chart in ("base","istio-control/istio-discovery"):
            if not (source/"manifests/charts"/chart/"Chart.yaml").is_file(): raise ValueError("matching Istio source charts missing")
        images += [audit(config["istioHub"]+"/"+name+":"+config["istioTag"]) for name in ("pilot","proxyv2")]
    run("kubectl","create","namespace",ns,"--dry-run=client","-o","json") # validated below via apply
    namespace={"apiVersion":"v1","kind":"Namespace","metadata":{"name":ns}}
    run("kubectl","apply","-f","-",data=json.dumps(namespace))
    probe=subprocess.run(["kubectl","get","secret","rv-grafana-admin","-n",ns],capture_output=True,text=True)
    if probe.returncode:
        if "NotFound" not in probe.stderr: raise RuntimeError(probe.stderr)
        secret={"apiVersion":"v1","kind":"Secret","metadata":{"name":"rv-grafana-admin","namespace":ns},
                "type":"Opaque","stringData":{"password":secrets.token_urlsafe(24)}}
        run("kubectl","create","-f","-",data=json.dumps(secret))
    values={k:config[k] for k in ("prometheusImage","grafanaImage","nodeTargets")}
    values["storage"]=config.get("storage",{"enabled":False})
    with tempfile.TemporaryDirectory(prefix="rv-helm-") as temp:
        path=pathlib.Path(temp,"values.json"); path.write_text(json.dumps(values))
        run("helm","lint","--strict",str(directory/"chart"),"-f",str(path))
        run("helm","upgrade","--install","rv-observability",str(directory/"chart"),"-n",ns,"-f",str(path),"--wait","--timeout","10m")
    if config.get("enableIstio",True):
        run("helm","upgrade","--install","istio-base",str(source/"manifests/charts/base"),"-n","istio-system","--create-namespace","--wait","--timeout","10m")
        run("helm","upgrade","--install","istiod",str(source/"manifests/charts/istio-control/istio-discovery"),
            "-n","istio-system","--set-string","global.hub="+config["istioHub"],
            "--set-string","global.tag="+config["istioTag"],"--wait","--timeout","10m")
    run("kubectl","rollout","status","deployment/rv-prometheus","-n",ns,"--timeout=300s")
    run("kubectl","rollout","status","deployment/rv-grafana","-n",ns,"--timeout=300s")
    print(json.dumps({"installedImages":images,"namespace":ns,"istioInstalled":config.get("enableIstio",True)},indent=2))
if __name__=="__main__": main()

