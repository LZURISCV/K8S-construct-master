#!/usr/bin/env python3
"""Real Prometheus target, Grafana provisioning and injected Istio sidecar checks."""
import json
import os
import subprocess
import urllib.request
from cases import Case

def get(url,headers=None):
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers or {}),timeout=15) as response:return json.load(response)
def main():
    c=Case("ecosystem");error=None
    try:
        p=get(os.environ["PROMETHEUS_URL"].rstrip("/")+"/api/v1/targets")
        targets=p["data"]["activeTargets"]
        node_targets=[t for t in targets if t["labels"].get("job")=="rv-nodes"]
        if not node_targets or any(t["health"]!="up" for t in node_targets):raise AssertionError("node scrape failed")
        url=os.environ["GRAFANA_URL"].rstrip("/")
        token=os.environ.get("GRAFANA_TOKEN")
        if token: headers={"Authorization":"Bearer "+token}
        else:
            import base64
            headers={"Authorization":"Basic "+base64.b64encode(("admin:"+os.environ["GRAFANA_PASSWORD"]).encode()).decode()}
        assert get(url+"/api/health")["database"]=="ok"
        assert get(url+"/api/dashboards/uid/rv-platform",headers)["dashboard"]["uid"]=="rv-platform"
        c.k.call("label","namespace",c.ns,"istio-injection=enabled")
        program="from http.server import HTTPServer,BaseHTTPRequestHandler\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b'mesh-ok')\nHTTPServer(('0.0.0.0',18080),H).serve_forever()"
        c.pod("mesh-app",{"containers":[{"name":"workload","image":c.image,"command":["python3","-c",program],
          "ports":[{"containerPort":18080,"name":"http"}],"resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"1","memory":"128Mi"}}}]},{"app":"mesh-server"})
        pod=c.ready("mesh-app")
        assert any(x["name"]=="istio-proxy" for x in pod["spec"]["containers"])
        c.k.create({"apiVersion":"v1","kind":"Service","metadata":{"name":"mesh-server","namespace":c.ns},
          "spec":{"selector":{"app":"mesh-server"},"ports":[{"name":"http","port":18080,"targetPort":18080}]}})
        c.pod("mesh-client");c.ready("mesh-client")
        result=c.k.call("exec","-n",c.ns,"mesh-client","-c","workload","--","python3","-c",
          "import json,urllib.request; data=urllib.request.urlopen('http://mesh-server:18080',timeout=10).read(); assert data==b'mesh-ok';print(json.dumps({'meshTraffic':True}))")
        status=subprocess.check_output(["istioctl","proxy-status"],text=True)
        matching=[line for line in status.splitlines() if "mesh-client."+c.ns in line]
        assert matching and "SYNCED" in matching[0],status
        c.record("real ecosystem health and mesh traffic",nodeTargets=len(node_targets),proxyInjected=True,dashboard="rv-platform",**result)
    except Exception as e:error=e
    finally:c.finish(error)
    if error:raise error
if __name__=="__main__":main()

