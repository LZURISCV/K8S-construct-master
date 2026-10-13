#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    if os.environ.get('ALLOW_PREEMPTION_TEST')!='1': raise Skip('需要 ALLOW_PREEMPTION_TEST=1 和独占测试工作节点')
    sys.path.insert(0,str(t.platform/'control'))
    from common import quantity
    existing=[p for p in t.items('pods',all_ns=True) if p['spec'].get('nodeName')==t.node and p.get('status',{}).get('phase') not in ('Succeeded','Failed')]
    if any(p['metadata']['namespace']!='kube-system' and not any(o['kind']=='DaemonSet' for o in p['metadata'].get('ownerReferences',[])) for p in existing): raise Skip('节点上还有无关业务 Pod；不执行抢占')
    if any(int(p['spec'].get('priority',0)) < -900 for p in existing): raise Skip('存在可能被抢占的无关 Pod')
    used=sum(sum(quantity(c.get('resources',{}).get('requests',{}).get('cpu','0')) for c in p['spec'].get('containers',[])+p['spec'].get('initContainers',[])) for p in existing)
    available=quantity(t.node_obj['status']['allocatable']['cpu'])-used
    if available<1: raise Skip('可用未预留 CPU 不足')
    t.values.update(LOW_CPU=str(int(available*600))+'m',HIGH_CPU=str(int(available*750))+'m')
    t.apply('priorities.yaml')
    t.cleanups.append(lambda: t.cmd('delete','priorityclass',t.ns+'-low',t.ns+'-high','--ignore-not-found'))
    t.apply('low.yaml'); low=t.ready('low')
    t.apply('high.yaml'); t.ready('high')
    def evicted():
        obj=t.get('pod','low',missing=True)
        return not obj or obj['metadata'].get('deletionTimestamp') or obj.get('status',{}).get('phase')=='Failed'
    t.wait(evicted,'低优先级 Pod 被抢占')
    t.record('preempted',sourceUID=low['metadata']['uid'])

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
