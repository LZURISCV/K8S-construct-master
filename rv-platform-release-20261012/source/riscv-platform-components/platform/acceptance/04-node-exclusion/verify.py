#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    key='platform.riscv.io/accept-'+t.nonce
    assert all(key not in n['metadata'].get('labels',{}) for n in t.items('nodes',cluster=True))
    t.apply('pod.yaml'); t.unscheduled('excluded')

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
