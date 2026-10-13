#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    import statistics
    target=os.environ.get('TARGET_NODE','')
    if not target or target==t.node:raise Skip('需设置第二个已安装组件并导入镜像的 TARGET_NODE')
    node=t.get('node',target,cluster=True)
    assert node['metadata']['labels'].get('kubernetes.io/arch')=='riscv64' and not node['spec'].get('unschedulable')
    assert any(c['type']=='Ready' and c['status']=='True' for c in node['status']['conditions'])
    t.values.update(TARGET_NODE=target,TARGET_HOSTNAME=node['metadata']['labels']['kubernetes.io/hostname'],CROSS_NET_PORT=os.environ.get('CROSS_NET_PORT','25214'))
    for f in ('pod-client.yaml','host-client.yaml','pod-server.yaml','host-server.yaml'):t.apply(f)
    ordinary={role:t.ready('pod-'+role) for role in ('client','server')}
    direct={}
    for role in ('client','server'):
        obj=t.wait(lambda role=role:(lambda o:o if o.get('status',{}).get('ready') else None)(t.get('hostnetworkworkload','host-'+role)),'host-'+role+' Ready')
        direct[role]=t.ready(obj['status']['pod'])
    assert ordinary['client']['spec']['nodeName']==direct['client']['spec']['nodeName']==t.node
    assert ordinary['server']['spec']['nodeName']==direct['server']['spec']['nodeName']==target
    t.same_images([p['status']['containerStatuses'][0]['imageID'] for d in (ordinary,direct) for p in d.values()])
    assert all(p['spec']['containers'][0]['resources']==ordinary['client']['spec']['containers'][0]['resources'] for d in (ordinary,direct) for p in d.values())
    samples={'pod':[],'host':[]}
    for trial in range(1,6):
        for mode in (('pod','host') if trial%2 else ('host','pod')):
            d=ordinary if mode=='pod' else direct
            raw=t.cmd('exec','-n',t.ns,d['client']['metadata']['name'],'--','iperf3','-c',d['server']['status']['podIP'],'-p',t.values['CROSS_NET_PORT'],'-t','20','-O','3','-P','2','-J')
            result=json.loads(raw);assert not result.get('error'),result
            (t.directory/('%02d-%s.json'%(trial,mode))).write_text(raw,encoding='utf-8')
            samples[mode].append(result['end']['sum_received']['bits_per_second']/1e6)
    med={k:statistics.median(v) for k,v in samples.items()}
    t.record('cross-node-throughput',mbitPerSecond=samples,median=med,hostToPodMedianRatio=med['host']/med['pod'],physicalNodes=[t.node,target],performanceImprovementClaimed=False)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
