#!/usr/bin/env python3
"""Explicitly resume an idempotent stage; never repeat an uncertain checkpoint."""
import argparse
from controller import GROUP, Kube, spec_hash, now

parser = argparse.ArgumentParser()
parser.add_argument("name")
parser.add_argument("-n", "--namespace", required=True)
parser.add_argument("--kubeconfig")
args = parser.parse_args()
kube = Kube(args.kubeconfig)
path = f"{GROUP}/namespaces/{args.namespace}/nodemigrations/{args.name}"
obj = kube.run("get", "--raw", path)
s = obj.get("status", {})
if s.get("phase") != "RecoveryRequired" or s.get("failedPhase") not in ("Transferring", "Restoring", "WaitingForRestore", "Committing"):
    raise SystemExit("仅支持重试传输、恢复等待和提交阶段；检查点阶段失败必须先人工检查源端。")
if spec_hash(obj["spec"]) != s["specHash"]:
    raise SystemExit("spec 已改变，拒绝重试。")
target = kube.pod(args.namespace, s["targetPod"])
if target and target.get("status", {}).get("phase") in ("Failed", "Succeeded"):
    raise SystemExit("目标已退出，可能已执行部分业务；保留状态并人工判断，不能直接重放检查点。")
s["phase"] = "Restoring" if s["failedPhase"] == "WaitingForRestore" else s["failedPhase"]
s["message"] = "administrator explicitly requested safe-stage retry"
s["retriedAt"] = now()
kube.status(obj, s)
print("已提交重试阶段:", s["phase"])
