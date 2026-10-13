#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    url=os.environ.get('PROMETHEUS_URL','http://'+os.environ['WORKER_IP']+':30090')
    target=os.environ['WORKER_IP']+':9108'
    command=[sys.executable,'-B',str(t.platform/'ecosystem/verify.py'),'--prometheus',url,'--target',target]
    result=subprocess.run(command,text=True,capture_output=True)
    (t.directory/'monitoring.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr
    t.record('real-metrics',prometheusURL=url,target=target,verification=json.loads(result.stdout))

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
