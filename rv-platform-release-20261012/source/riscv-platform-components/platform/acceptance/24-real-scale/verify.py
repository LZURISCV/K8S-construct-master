#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    count=int(os.environ.get('SCALE_PODS','10'))
    if count<1:raise ValueError('SCALE_PODS 必须大于零')
    live=[p for p in t.items('pods',all_ns=True) if p['spec'].get('nodeName')==t.node and p.get('status',{}).get('phase') not in ('Failed','Succeeded')]
    capacity=int(t.node_obj['status']['allocatable']['pods'])-len(live)
    if count>capacity:raise Skip('节点可用 Pod 槽位只有 %s，申请 %s；扩容实际节点后再测'%(capacity,count))
    t.values['SCALE_PODS']=str(count)
    start=time.monotonic();t.apply('deployment.yaml');t.rollout('scale',timeout=1800)
    pods=[p for p in t.items('pods') if p['metadata'].get('labels',{}).get('app')=='scale' and t.is_ready(p)]
    assert len(pods)==count and len({p['metadata']['uid'] for p in pods})==count
    t.record('real-pod-scale',requested=count,ready=len(pods),elapsedSeconds=time.monotonic()-start,physicalNodes=sorted({p['spec']['nodeName'] for p in pods}),ultraScaleClaimed=False)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
