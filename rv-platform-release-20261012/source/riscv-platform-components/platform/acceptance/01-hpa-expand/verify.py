#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.apply('deployment.yaml')
    t.rollout('load')
    t.apply('hpa.yaml')
    p=t.items('pods')[0]['metadata']['name']
    t.cmd('exec','-n',t.ns,p,'--','touch','/tmp/enable_pressure')
    t.wait(lambda: t.get('deployment','load')['spec']['replicas'] > 1 and t.get('deployment','load').get('status',{}).get('readyReplicas',0)>1, 'HPA 扩容且新增副本就绪')
    t.record('expanded',deployment=t.get('deployment','load'),hpa=t.get('hpa','load'))

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
