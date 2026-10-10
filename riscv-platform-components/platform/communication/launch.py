#!/usr/bin/env python3
"""Select TCP plus available intra-node shared memory, then exec mpirun."""
import os
import subprocess
import sys

def tcp_components(info):
    shared=next((name for name in ("sm","vader") if "mca:btl:"+name+":" in info),None)
    return ",".join(["self","tcp"]+([shared] if shared else []))

def main():
    info=subprocess.check_output(["ompi_info","--parsable","--param","btl","all"],text=True)
    os.execvp("mpirun",["mpirun","--mca","pml","ob1","--mca","btl",tcp_components(info),*sys.argv[1:]])
if __name__=="__main__": main()
