#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    key='platform.riscv.io/accept-'+t.nonce
    assert key not in t.node_obj['metadata'].get('labels',{})
    t.prepare_profile()
    t.apply_profile('profile.yaml')
    t.wait(lambda:t.get('node',t.node,cluster=True)['metadata'].get('labels',{}).get(key)=='first','首次节点标签生效')
    t.apply_profile('profile-updated.yaml')
    t.wait(lambda:t.get('node',t.node,cluster=True)['metadata'].get('labels',{}).get(key)=='second','节点标签更新生效')
    t.record('profile',object=t.get('nodeprofile',t.values['PROFILE_NAME'],namespace=t.values['PROFILE_NS']))

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
