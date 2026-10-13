#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.values['NET_SERVER_PORT']=os.environ.get('NET_SERVER_PORT','25211')
    port=int(t.values['NET_SERVER_PORT'])
    if any(p.get('hostPort')==port for o in t.items('hostnetworkworkloads',all_ns=True) if o['spec']['node']==t.node for p in o['spec'].get('ports',[])): raise Skip('测试端口已被其他自研工作负载占用')
    t.apply('server.yaml')
    first=t.wait(lambda:(lambda o:o if o.get('status',{}).get('ready') else None)(t.get('hostnetworkworkload','tcp-server')),'首个端口申请 Ready')
    t.apply('peer.yaml')
    def conflict():
        obj=t.get('hostnetworkworkload','tcp-peer')
        if obj.get('status',{}).get('ready'):raise AssertionError('重复端口被错误分配')
        return obj if 'reserved' in obj.get('status',{}).get('message','').lower() else None
    t.record('conflict',object=t.wait(conflict,'重复端口申请被拒绝'))
    t.cmd('delete','hostnetworkworkload','tcp-server','-n',t.ns,'--wait=true','--timeout=180s')
    t.wait(lambda:t.get('pod',first['status']['pod'],missing=True) is None,'旧 Pod 删除后端口释放')
    t.record('released-and-reallocated',object=t.wait(lambda:(lambda o:o if o.get('status',{}).get('ready') else None)(t.get('hostnetworkworkload','tcp-peer')),'第二申请获得已释放端口'))

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
