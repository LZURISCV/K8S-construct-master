#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    import statistics
    if not os.environ.get('MASTER_IP'): raise ValueError('需要 MASTER_IP')
    port=int(os.environ.get('NET_CLIENT_PORT','25212'))
    server=subprocess.Popen(['iperf3','-s','-p',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    try:
        time.sleep(1)
        if server.poll() is not None: raise RuntimeError('控制端 iperf3 无法监听：'+server.stderr.read())
        t.apply('baseline.yaml'); base=t.ready('pod-client')
        t.apply('host-client.yaml')
        obj=t.wait(lambda:(lambda o:o if o.get('status',{}).get('ready') else None)(t.get('hostnetworkworkload','host-client')),'主机网络 Pod 就绪')
        direct=t.ready(obj['status']['pod'])
        assert base['spec']['nodeName']==direct['spec']['nodeName']==t.node
        assert not base['spec'].get('hostNetwork',False) and direct['spec'].get('hostNetwork') is True
        assert base['spec']['containers'][0]['resources']==direct['spec']['containers'][0]['resources']
        t.same_images([p['status']['containerStatuses'][0]['imageID'] for p in (base,direct)])
        rates={'pod':[],'host':[]}; names={'pod':'pod-client','host':direct['metadata']['name']}
        for trial in range(1,6):
            for mode in (('pod','host') if trial%2 else ('host','pod')):
                print('第 %s/5 轮 %s'%(trial,mode),flush=True)
                raw=t.cmd('exec','-n',t.ns,names[mode],'--','iperf3','-c',os.environ['MASTER_IP'],'-p',str(port),'-t','20','-O','3','-P','2','-J')
                result=json.loads(raw); assert not result.get('error'),result
                (t.directory/('%02d-%s.json'%(trial,mode))).write_text(raw,encoding='utf-8')
                value=result['end']['sum_received']['bits_per_second']/1e6; rates[mode].append(value)
                print('接收吞吐量 %.2f Mbit/s'%value,flush=True)
        med={k:statistics.median(v) for k,v in rates.items()}
        t.record('paired-throughput',mbitPerSecond=rates,median=med,hostToPodMedianRatio=med['host']/med['pod'],performanceImprovementClaimed=False)
    finally:
        server.terminate()
        try:server.wait(timeout=5)
        except subprocess.TimeoutExpired:server.kill();server.wait()

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
