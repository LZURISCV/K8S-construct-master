#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.apply('pod.yaml');t.ready('limited')
    program="\nimport json,pathlib\ngroups={}\nfor line in pathlib.Path('/proc/self/cgroup').read_text().splitlines():\n _,controllers,path=line.split(':',2)\n for controller in controllers.split(','):groups[controller]=path\nmounts={}\nfor line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines():\n before,after=line.split(' - ',1);left=before.split();right=after.split()\n if right[0]!='cgroup':continue\n for controller in right[2].split(','):\n  if controller in groups:\n   root=pathlib.PurePosixPath(left[3]);group=pathlib.PurePosixPath(groups[controller])\n   relative=group.relative_to(root)\n   mounts[controller]=pathlib.Path(left[4])/str(relative)\nresult={'cpuQuota':int((mounts['cpu']/'cpu.cfs_quota_us').read_text()),\n 'cpuPeriod':int((mounts['cpu']/'cpu.cfs_period_us').read_text()),\n 'memoryLimit':int((mounts['memory']/'memory.limit_in_bytes').read_text()),'cgroupVersion':1}\nprint(json.dumps(result))\n"
    result=json.loads(t.cmd('exec','-n',t.ns,'limited','--','python3','-c',program))
    assert result['memoryLimit']==64*1024*1024
    assert abs(result['cpuQuota']/result['cpuPeriod']-.2)<1e-6
    t.record('effective-cgroup-limits',**result)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
