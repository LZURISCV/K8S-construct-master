#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from support import Test, run, Skip

def test(t):
    required=['nodeprofiles.platform.riscv.io','resourceclaims.platform.riscv.io','resourcepolicies.platform.riscv.io','scenariopolicies.platform.riscv.io','hostnetworkworkloads.platform.riscv.io','nodemigrations.migration.riscv.io']
    objects={name:t.get('crd',name,cluster=True) for name in required}
    t.record('installed-crds',objects=objects,communityContributionVerified=False)

if __name__ == '__main__':
    run(test, pathlib.Path(__file__).resolve().parent)
