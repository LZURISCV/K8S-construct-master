#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.apply('source.yaml');t.apply('job.yaml')
    def done():
        o=t.get('job','compute')
        if o.get('status',{}).get('failed',0):return o
        return o if o.get('status',{}).get('succeeded',0) else None
    t.wait(done,'计算任务结束')
    raw=t.cmd('logs','-n',t.ns,'job/compute','-c','compute')
    (t.directory/'compute.json').write_text(raw,encoding='utf-8')
    result=json.loads(raw)
    t.record('compute-deadline',result=result)
    assert result['deadline_misses']==0,result
    assert result['hardRealtimeGuarantee'] is False

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
