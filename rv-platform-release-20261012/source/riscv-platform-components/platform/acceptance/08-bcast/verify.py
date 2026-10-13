#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    workers=int(os.environ.get('MPI_WORKERS','1')); slots=2
    t.values.update(MPI_WORKERS=str(workers),MPI_RANKS=str(workers*slots))
    if workers<1: raise ValueError('MPI_WORKERS 必须为正数')
    available=[n for n in t.items('nodes',cluster=True) if n['metadata']['labels'].get('kubernetes.io/arch')=='riscv64' and not n['spec'].get('unschedulable') and any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions'])]
    if workers>len(available):raise Skip('MPI 工作副本要求不同物理节点，当前可用节点不足')
    results={}
    for variant in ['native', 'hierarchical']:
        t.apply(variant+'.yaml')
        name='mpi-bcast-'+variant
        def completed():
            o=t.get('mpijob',name); conditions=o.get('status',{}).get('conditions',[])
            if any(x['type']=='Failed' and x['status']=='True' for x in conditions): raise AssertionError(o['status'])
            return o if any(x['type']=='Succeeded' and x['status']=='True' for x in conditions) else None
        obj=t.wait(completed,name+' Succeeded')
        pods=t.items('pods'); launches=[p for p in pods if p['metadata']['name'].startswith(name+'-launcher-')]
        assert len(launches)==1
        logs=t.cmd('logs','-n',t.ns,launches[0]['metadata']['name'],'-c','mpi')
        (t.directory/(variant+'.log')).write_text(logs,encoding='utf-8')
        messages=[json.loads(line) for line in logs.splitlines() if line.startswith('{') and '"operation"' in line]
        assert len(messages)==1,logs
        result=messages[0]
        assert result['operation']=='bcast' and result['correct'] is True and result['ranks']==workers*slots,result
        assert result['mantissa_bits']>=113,result
        worker_pods=[p for p in pods if p['metadata']['name'].startswith(name+'-worker-')]
        assert len(worker_pods)==workers
        assert len({p['spec']['nodeName'] for p in worker_pods})==workers
        names=[p['metadata']['name'] for p in worker_pods]
        assert sorted(result['worker_pod_names'])==sorted(names*slots),result
        images=[c['imageID'] for p in worker_pods+launches for c in p['status']['containerStatuses']]
        t.same_images(images)
        results[variant]=result
        t.record(variant,mpijob=obj,result=result,workerPlacement={p['metadata']['name']:p['spec']['nodeName'] for p in worker_pods})
        t.cmd('delete','mpijob',name,'-n',t.ns,'--wait=true','--timeout=180s')
        t.wait(lambda: not any(p['metadata']['name'].startswith(name+'-') for p in t.items('pods')), '前一任务 Pod 已释放')
    t.record('comparison',measurements=results,performanceImprovementClaimed=False)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
