#!/usr/bin/env python3
"""Read-only Linux HAL and Prometheus exporter."""
import argparse
import json
import os
import pathlib
import platform
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def memory(root="/proc"):
    data={}
    for line in pathlib.Path(root,"meminfo").read_text().splitlines():
        key,value=line.split(":",1)
        data[key]=int(value.strip().split()[0])*1024
    return data

def cpu(root="/proc"):
    values=list(map(int,pathlib.Path(root,"stat").read_text().splitlines()[0].split()[1:]))
    return sum(values[:8]),values[3]+values[4]

def describe():
    m=memory()
    return {"architecture":platform.machine(),"kernel":platform.release(),
      "cpuCount":os.cpu_count(),"memoryBytes":m["MemTotal"],
      "cgroupVersion":2 if pathlib.Path("/sys/fs/cgroup/cgroup.controllers").exists() else 1,
      "backends":["kubernetes.cpu","kubernetes.memory","kubernetes.extended-resource"],
      "devices":sorted(p.name for p in pathlib.Path("/sys/class/net").iterdir())}

def metrics(previous,root="/proc"):
    total,idle=cpu(root); oldtotal,oldidle=previous
    utilization=max(0,min(1,1-(idle-oldidle)/max(1,total-oldtotal)))
    m=memory(root)
    lines=[f"rv_node_cpu_utilization_ratio {utilization:.9f}",
      f"rv_node_memory_total_bytes {m['MemTotal']}",f"rv_node_memory_available_bytes {m['MemAvailable']}",
      f"rv_node_load1 {pathlib.Path(root,'loadavg').read_text().split()[0]}",
      f"rv_node_cpu_count {os.cpu_count()}",
      f"rv_node_cgroup_version {2 if pathlib.Path('/sys/fs/cgroup/cgroup.controllers').exists() else 1}",
      f"rv_node_sample_timestamp_seconds {time.time():.6f}"]
    return "\n".join(lines)+"\n",(total,idle)

def serve(host,port):
    import threading
    state={"text":"","previous":cpu()}
    def sampler():
        while True:
            time.sleep(1)
            try: state["text"],state["previous"]=metrics(state["previous"])
            except Exception as error: print(error,flush=True)
    threading.Thread(target=sampler,daemon=True).start()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!="/metrics": self.send_error(404); return
            body=state["text"].encode()
            if not body: self.send_error(503); return
            self.send_response(200); self.send_header("Content-Type","text/plain; version=0.0.4")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args): pass
    ThreadingHTTPServer((host,port),Handler).serve_forever()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("command",choices=["describe","exporter"])
    p.add_argument("--listen",default="127.0.0.1")
    p.add_argument("--port",type=int,default=9108)
    a=p.parse_args()
    if a.command=="exporter": serve(a.listen,a.port)
    elif a.command=="describe": print(json.dumps(describe()))
if __name__=="__main__": main()

