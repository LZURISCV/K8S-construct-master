#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.apply('deployment.yaml'); t.rollout('scene-app')
    t.apply('high.yaml')
    t.wait(lambda:t.get('deployment','scene-app')['spec']['replicas']==3 and t.get('deployment','scene-app').get('status',{}).get('readyReplicas',0)==3,'高值扩展到三个就绪副本')
    t.record('high',policy=t.get('resourcepolicy','feedback'))
    t.apply('low.yaml')
    t.wait(lambda:t.get('deployment','scene-app')['spec']['replicas']==1 and t.get('deployment','scene-app').get('status',{}).get('readyReplicas',0)==1,'低值收缩到一个就绪副本')
    t.record('low',policy=t.get('resourcepolicy','feedback'),functionalInjection=True)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
