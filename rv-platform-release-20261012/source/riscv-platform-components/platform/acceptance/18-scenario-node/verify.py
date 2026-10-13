#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    key='platform.riscv.io/accept-'+t.nonce
    assert key not in t.node_obj['metadata'].get('labels',{})
    t.prepare_profile(scenario=True)
    t.apply_profile('profile.yaml')
    t.cleanups.append(lambda:t.cmd('delete','scenariopolicy',t.values['PROFILE_POLICY'],'-n',t.values['PROFILE_NS'],'--ignore-not-found','--wait=true'))
    # The action replaces the profile label map. Carry over every preexisting
    # label rather than dropping a production pool label during the test.
    filename=t.render_file('policy.yaml')
    desired=json.loads(t.cmd('create','--dry-run=client','--validate=false','-f',str(filename),'-o','json'))
    current=t.get('nodeprofile',t.values['PROFILE_NAME'],namespace=t.values['PROFILE_NS'])
    desired['spec']['highAction']['labels']={**current['spec'].get('labels',{}),key:'hot'}
    t.command(['create','-f','-'],input=json.dumps(desired))
    t.wait(lambda:t.get('node',t.node,cluster=True)['metadata'].get('labels',{}).get(key)=='hot','场景策略通过 NodeProfile 修改节点配置')
    t.record('configured',profile=t.get('nodeprofile',t.values['PROFILE_NAME'],namespace=t.values['PROFILE_NS']),policy=t.get('scenariopolicy',t.values['PROFILE_POLICY'],namespace=t.values['PROFILE_NS']),functionalInjection=True)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
