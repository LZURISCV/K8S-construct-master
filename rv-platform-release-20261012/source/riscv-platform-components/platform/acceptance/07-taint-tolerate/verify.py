#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    key='acceptance.riscv.io/taint-'+t.nonce
    t.cmd('taint','node',t.node,key+'=yes:NoSchedule')
    t.cleanups.append(lambda: t.cmd('taint','node',t.node,key+'-'))
    t.apply('pod.yaml')
    assert t.ready("tainted")["spec"]["nodeName"]==t.node

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
