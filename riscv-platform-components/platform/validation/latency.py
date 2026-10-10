#!/usr/bin/env python3
"""TCP data-plane measurements with exact echo/hash checks and explicit deadlines."""
import argparse
import hashlib
import json
import math
import os
import socket
import struct
import time
from pathlib import Path

def receive(sock,n):
    data=bytearray()
    while len(data)<n:
        chunk=sock.recv(n-len(data))
        if not chunk: raise ConnectionError("truncated echo")
        data.extend(chunk)
    return data

def percentile(values,p):
    ordered=sorted(values); return ordered[min(len(ordered)-1,max(0,math.ceil(len(ordered)*p)-1))]

def server(host,port):
    import threading
    def handle(connection):
        with connection:
            while True:
                try:
                    size=struct.unpack("!Q",receive(connection,8))[0]
                    if not 1<=size<=64*1024*1024: return
                    connection.sendall(receive(connection,size))
                except (ConnectionError,OSError): return
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);sock.bind((host,port));sock.listen(128)
        while True:
            conn,_=sock.accept();threading.Thread(target=handle,args=(conn,),daemon=True).start()

def client(host,port,count,size,deadline_ms,output,fifo=None):
    if count<1 or not 1<=size<=64*1024*1024: raise ValueError("invalid sample count/size")
    if fifo is not None:
        os.sched_setscheduler(0,os.SCHED_FIFO,os.sched_param(fifo))
    payload=bytes((i%251 for i in range(size))); digest=hashlib.sha256(payload).hexdigest()
    samples=[]; started=time.perf_counter()
    with socket.create_connection((host,port),timeout=10) as sock:
        sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        # Warmup excluded.
        sock.sendall(struct.pack("!Q",size)+payload); assert receive(sock,size)==payload
        for index in range(count):
            begin=time.perf_counter_ns();sock.sendall(struct.pack("!Q",size)+payload);data=receive(sock,size)
            elapsed=(time.perf_counter_ns()-begin)/1e6
            if hashlib.sha256(data).hexdigest()!=digest: raise AssertionError("data mismatch")
            samples.append(elapsed)
    elapsed=time.perf_counter()-started
    result={"operation":"tcp-echo","host":host,"port":port,"count":count,"bytes":size,"p50_ms":percentile(samples,.5),
            "p99_ms":percentile(samples,.99),"max_ms":max(samples),"deadline_ms":deadline_ms,
            "deadline_misses":sum(x>deadline_ms for x in samples) if deadline_ms else None,
            "roundtrip_payload_mbps":count*size*8/elapsed/1e6,"elapsed_seconds":elapsed,
            "fifoPriority":fifo,"correct":True,"samples_ms":samples,"evidence":"real-TCP","hardRealtimeGuarantee":False}
    Path(output).write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!="samples_ms"}))
    return 1 if deadline_ms and result["deadline_misses"] else 0

def main():
    p=argparse.ArgumentParser()
    p.add_argument("mode",choices=["server","client"]);p.add_argument("--host",default="0.0.0.0");p.add_argument("--port",type=int,default=19090)
    p.add_argument("--count",type=int,default=1000);p.add_argument("--bytes",type=int,default=4096)
    p.add_argument("--deadline-ms",type=float);p.add_argument("--fifo-priority",type=int);p.add_argument("--output",default="latency.json")
    a=p.parse_args()
    if a.mode=="server": server(a.host,a.port)
    else: raise SystemExit(client(a.host,a.port,a.count,a.bytes,a.deadline_ms,a.output,a.fifo_priority))
if __name__=="__main__": main()

