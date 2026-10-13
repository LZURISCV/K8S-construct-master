#!/usr/bin/env python3
"""Write a dedicated service account kubeconfig (run on control host only)."""
import base64
import json
import pathlib
import subprocess
import sys
import time
def kubectl(*args):
    return json.loads(subprocess.check_output(["kubectl",*args],text=True))
def main():
    name,destination=sys.argv[1:]
    cluster=kubectl("config","view","--raw","--minify","--flatten","-o","json")["clusters"][0]["cluster"]
    if cluster.get("insecure-skip-tls-verify"): raise ValueError("verified API TLS required")
    for _ in range(30):
        secret=kubectl("get","secret",name+"-token","-n","rv-platform","-o","json")
        if secret.get("data",{}).get("token"): break
        time.sleep(1)
    token=base64.b64decode(secret["data"]["token"]).decode()
    out={"apiVersion":"v1","kind":"Config","clusters":[{"name":"cluster","cluster":cluster}],
         "users":[{"name":name,"user":{"token":token}}],
         "contexts":[{"name":"default","context":{"cluster":"cluster","user":name}}],"current-context":"default"}
    path=pathlib.Path(destination); path.parent.mkdir(parents=True,exist_ok=True)
    # umask is set by installer before opening file.
    path.write_text(json.dumps(out,indent=2)); path.chmod(0o600)
if __name__=="__main__": main()

