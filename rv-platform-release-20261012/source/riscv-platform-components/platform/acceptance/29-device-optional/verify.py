#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    resource=os.environ.get('DEVICE_RESOURCE','');path=os.environ.get('DEVICE_CONTAINER_PATH','')
    if not resource or not path:raise Skip('未配置真实设备资源；本次软件网络方案不依赖设备插件')
    import re
    if not re.fullmatch(r'[a-zA-Z0-9./_-]+',resource) or not re.fullmatch(r'/dev/[a-zA-Z0-9/_-]+',path):raise ValueError('非法设备名称或路径')
    count=int(t.node_obj['status']['allocatable'].get(resource,0))
    if count<1:raise Skip('节点没有注册可用的真实设备')
    t.values.update(DEVICE_RESOURCE=resource,DEVICE_CONTAINER_PATH=path,DEVICE_COUNT=str(count))
    t.apply('one.yaml');t.ready('one')
    t.apply('all.yaml');t.unscheduled('all')
    t.cmd('delete','pod','one','-n',t.ns,'--wait=true','--timeout=180s')
    t.ready('all');t.record('exclusive-device-released',resource=resource,allocatable=count,containerPath=path)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
