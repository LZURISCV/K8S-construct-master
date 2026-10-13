#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.apply('anchor.yaml'); a=t.ready('anchor')
    t.apply('follower.yaml'); b=t.ready('follower')
    assert a['spec']['nodeName']==b['spec']['nodeName']
    t.record('same-node',anchor=a,follower=b)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
