#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    t.values['LATENCY_PORT']=os.environ.get('LATENCY_PORT','25213')
    t.apply('server.yaml')
    obj=t.wait(lambda:(lambda o:o if o.get('status',{}).get('ready') else None)(t.get('hostnetworkworkload','echo')),'回显服务就绪')
    target=obj['status']['nodeIP']
    args=[sys.executable,str(t.platform/'validation/latency.py'),'client','--host',target,'--port',t.values['LATENCY_PORT'],'--count','1000','--bytes','4096','--deadline-ms',os.environ.get('LATENCY_DEADLINE_MS','10'),'--output',str(t.directory/'latency.json')]
    result=subprocess.run(args,text=True,capture_output=True)
    (t.directory/'client.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((t.directory/'latency.json').read_text())
    assert report['correct'] is True and report['count']==1000
    t.record('measured-tcp-latency',result=report)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
