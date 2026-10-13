#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.apply('pod.yaml'); assert t.ready('target')['spec']['nodeName']==t.node

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
