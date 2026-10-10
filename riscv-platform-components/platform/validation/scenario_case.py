#!/usr/bin/env python3
"""Functional feedback test uses real Prometheus vector() samples, not performance evidence."""
import json
import os
from cases import Case

def main():
    c=Case("scenario");error=None
    try:
        c.k.create({"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"scene-app","namespace":c.ns,
          "annotations":{"platform.riscv.io/allow-scaling":"feedback"}},"spec":{"replicas":1,"selector":{"matchLabels":{"app":"scene"}},
          "template":{"metadata":{"labels":{"app":"scene"}},"spec":{"containers":[{"name":"app","image":c.image,"command":["sleep","86400"],
            "resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"1","memory":"64Mi"}}}]}}}})
        c.k.call("rollout","status","deployment/scene-app","-n",c.ns,"--timeout=300s")
        policy={"apiVersion":"platform.riscv.io/v1alpha1","kind":"ScenarioPolicy","metadata":{"name":"feedback","namespace":c.ns},
          "spec":{"query":"vector(0.9)","low":0.3,"high":0.8,"consecutiveSamples":2,"cooldownSeconds":0,
            "minReplicas":1,"maxReplicas":3,"highAction":{"type":"scale","deployment":"scene-app","replicas":3},
            "lowAction":{"type":"scale","deployment":"scene-app","replicas":1}}}
        c.k.create(policy)
        c.wait(lambda:c.k.get("deployment","scene-app",c.ns)["spec"]["replicas"]==3,"high samples caused scale up")
        obj=c.k.get("scenariopolicy","feedback",c.ns)
        c.k.patch("scenariopolicy",obj,{"spec":{**obj["spec"],"query":"vector(0.1)"}})
        c.wait(lambda:c.k.get("deployment","scene-app",c.ns)["spec"]["replicas"]==1,"low samples caused scale down")
        c.record("functional injection only",querySource="Prometheus vector(0.9)/vector(0.1)")
    except Exception as e: error=e
    finally: c.finish(error)
    if error: raise error
if __name__=="__main__":main()

