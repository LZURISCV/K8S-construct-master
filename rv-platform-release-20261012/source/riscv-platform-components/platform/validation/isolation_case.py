#!/usr/bin/env python3
"""Check effective cgroup v1 CPU and memory limits inside a real container."""
import json
from cases import Case

PROGRAM=r"""
import json,pathlib
groups={}
for line in pathlib.Path('/proc/self/cgroup').read_text().splitlines():
 _,controllers,path=line.split(':',2)
 for controller in controllers.split(','):groups[controller]=path
mounts={}
for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines():
 before,after=line.split(' - ',1);left=before.split();right=after.split()
 if right[0]!='cgroup':continue
 for controller in right[2].split(','):
  if controller in groups:
   root=pathlib.PurePosixPath(left[3]);group=pathlib.PurePosixPath(groups[controller])
   relative=group.relative_to(root)
   mounts[controller]=pathlib.Path(left[4])/str(relative)
result={'cpuQuota':int((mounts['cpu']/'cpu.cfs_quota_us').read_text()),
 'cpuPeriod':int((mounts['cpu']/'cpu.cfs_period_us').read_text()),
 'memoryLimit':int((mounts['memory']/'memory.limit_in_bytes').read_text()),'cgroupVersion':1}
print(json.dumps(result))
"""
def main():
    c=Case("isolation");error=None
    try:
        c.pod("limited",{"nodeSelector":c.target(),"containers":[{"name":"workload","image":c.image,
           "command":["sleep","86400"],"resources":{"requests":{"cpu":"100m","memory":"32Mi"},
             "limits":{"cpu":"200m","memory":"64Mi"}}}]})
        c.ready("limited")
        # Use subprocess directly since kubectl exec produces application JSON.
        result=c.k.call("exec","-n",c.ns,"limited","--","python3","-c",PROGRAM)
        assert result["memoryLimit"]==64*1024*1024,result
        assert abs(result["cpuQuota"]/result["cpuPeriod"]-.2)<1e-6,result
        c.record("effective cgroup v1 limits verified",**result)
    except Exception as e:error=e
    finally:c.finish(error)
    if error:raise error
if __name__=="__main__":main()

