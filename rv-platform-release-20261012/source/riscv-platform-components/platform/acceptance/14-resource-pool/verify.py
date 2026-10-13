#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.apply('claim.yaml')
    t.wait(lambda:t.get('resourceclaim','pool').get('status',{}).get('phase')=='Ready','资源声明 Ready')
    old=t.wait(lambda:next((p for p in t.items('pods') if t.is_ready(p)),None),'资源 Pod Ready')
    t.apply('claim-resized.yaml')
    def updated():
        return next((p for p in t.items('pods') if p['metadata']['uid']!=old['metadata']['uid'] and p['spec']['containers'][0]['resources']['limits']['cpu']=='200m' and t.is_ready(p)),None)
    new=t.wait(updated,'资源更新通过滚动创建新 Pod 生效')
    t.record('rollout-resize',sourceUID=old['metadata']['uid'],targetUID=new['metadata']['uid'],claim=t.get('resourceclaim','pool'))

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
