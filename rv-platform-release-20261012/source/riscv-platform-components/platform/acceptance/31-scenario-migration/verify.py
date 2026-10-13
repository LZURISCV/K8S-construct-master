#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    mode='CrossNode'
    if mode=='CrossNode':
        target=os.environ.get('TARGET_NODE','')
        if not target or target==t.node:raise Skip('跨节点需设置不同的 TARGET_NODE 并安装同版本迁移插件')
        node=t.get('node',target,cluster=True)
        assert not node['spec'].get('unschedulable') and any(x['type']=='Ready' and x['status']=='True' for x in node['status']['conditions'])
        t.values['TARGET_NODE']=target
    t.apply('source-pod.yaml'); source=t.ready('memory-counter')
    def sample(name):
        raw=t.cmd('logs','-n',t.ns,name,'--tail=5')
        return [json.loads(x) for x in raw.splitlines() if x.startswith('{')]
    before=t.wait(lambda:sample('memory-counter'),'源内存计数输出')[-1]
    (t.directory/'before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
    t.apply('scenario.yaml')
    migration=t.wait(lambda:next((o for o in t.items('nodemigrations') if o['spec']['sourcePod']=='memory-counter'),None),'场景控制器自动生成迁移请求')
    migration_name=migration['metadata']['name']
    t.cmd('delete','scenariopolicy','feedback','-n',t.ns,'--wait=true')
    def complete():
        obj=t.get('nodemigration',migration_name); phase=obj.get('status',{}).get('phase')
        print('迁移阶段：'+str(phase),flush=True)
        if phase in ('Failed','RecoveryRequired'): raise AssertionError(obj)
        return obj if phase=='Succeeded' else None
    obj=t.wait(complete,'迁移事务 Succeeded',timeout=1800)
    st=obj['status']; target=t.ready(st['targetPod'])
    t.wait(lambda:len(sample(st['targetPod']))>=2,'恢复后至少两个内存计数样本')
    after=sample(st['targetPod'])
    assert st['sourceUID']==source['metadata']['uid'] and st['targetUID']==target['metadata']['uid'] and st['sourceUID']!=st['targetUID']
    assert (st['sourceNode']==st['targetNode'])==(mode=='SameNode')
    if mode=='SameNode':assert st.get('transferSkipped') is True
    assert all(x['boot_id']==before['boot_id'] for x in after)
    assert after[-1]['counter']>before['counter'] and all(a['counter']<b['counter'] for a,b in zip(after,after[1:]))
    t.record('memory-continuity',mode=mode,migration=obj,before=before,after=after,observedGapSeconds=(after[0]['time_ns']-before['time_ns'])/1e9,gapIsExactPause=False)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
