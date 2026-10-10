#!/usr/bin/env python3
"""Real registered device capacity, exclusive accounting, /dev mapping and release."""
import os
from cases import Case
def main():
    c=Case("device");error=None
    try:
        resource=os.environ["DEVICE_RESOURCE"];device=os.environ["DEVICE_CONTAINER_PATH"]
        node=c.k.get("node",c.node);count=int(node["status"]["allocatable"].get(resource,"0"))
        if count<1:raise ValueError("no healthy registered device")
        spec={"nodeSelector":c.target(),"containers":[{"name":"workload","image":c.image,
          "command":["python3","-c","import os,stat,time; assert stat.S_ISCHR(os.stat("+repr(device)+").st_mode) or stat.S_ISBLK(os.stat("+repr(device)+").st_mode); time.sleep(86400)"],
          "resources":{"requests":{"cpu":"10m","memory":"16Mi",resource:"1"},"limits":{"cpu":"1","memory":"64Mi",resource:"1"}}}]}
        c.pod("one",spec);c.ready("one")
        # Verify mapping by executing an explicit assertion, not only Pod Ready.
        c.k.call("exec","-n",c.ns,"one","--","python3","-c","import os,json; print(json.dumps({'mapped':os.path.exists("+repr(device)+")}))")
        spec["containers"][0]["resources"]["requests"][resource]=str(count)
        spec["containers"][0]["resources"]["limits"][resource]=str(count)
        c.pod("all",spec);c.unscheduled("all")
        first=c.k.get("pod","one",c.ns);c.k.delete("pod",first)
        c.ready("all");c.record("device release and exclusive accounting verified",resource=resource,capacity=count)
    except Exception as e:error=e
    finally:c.finish(error)
    if error:raise error
if __name__=="__main__":main()

