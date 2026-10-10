#!/usr/bin/env python3
"""Produce comparison report from raw measured JSON; no invented results."""
import argparse
import json
import math
import pathlib

def comparisons(records):
    groups={}
    for record in records:
        if record.get("operation") not in ("bcast","reduce","allgather","fusion"): continue
        key=json.dumps({k:record.get(k) for k in ("operation","limited","settings","workerNodes","image","imageID","mpiArgs","mantissa_bits","networkMode","tcpNetwork","transport")},sort_keys=True)
        groups.setdefault(key,{}).setdefault(record["mode"],[]).append(record)
    rows=[]
    for key,values in groups.items():
        config=json.loads(key);baseline="serial" if config["operation"]=="fusion" else "native"
        candidate="overlap" if baseline=="serial" else "hierarchical"
        if baseline not in values or candidate not in values: continue
        left,right=values[baseline],values[candidate]
        if not all(x.get("correct") and x.get("evidence")=="real-MPIJob" for x in left+right): continue
        a=sum(x["mean_seconds"] for x in left)/len(left);b=sum(x["mean_seconds"] for x in right)/len(right)
        rows.append({**config,"baseline":baseline,"candidate":candidate,"baseline_seconds":a,"candidate_seconds":b,
                     "speedup":a/b if b>0 else None,"samples":[len(left),len(right)]})
    return rows

def network_comparisons(records):
    groups={}
    keys=("comparisonId","clientNode","serverNode","image","imageID","seconds","streams","port","resources")
    for r in records:
        if r.get("operation")!="iperf3" or r.get("evidence")!="real-iperf3" or r.get("mode") not in ("pod","host"): continue
        if not all(r.get(k) for k in keys) or r["clientNode"]==r["serverNode"]: continue
        if r.get("hostNetwork") is not (r["mode"]=="host"): continue
        rate=r.get("bitsPerSecond")
        if isinstance(rate,bool) or not isinstance(rate,(int,float)) or not math.isfinite(rate) or rate<=0: continue
        if isinstance(r.get("trial"),bool) or not isinstance(r.get("trial"),int) or r["trial"]<0: continue
        key=json.dumps({k:r[k] for k in keys},sort_keys=True)
        groups.setdefault(key,{}).setdefault(r["mode"],[]).append(r)
    rows=[]
    for key,modes in groups.items():
        if set(modes)!={"pod","host"}: continue
        trials={mode:[r["trial"] for r in samples] for mode,samples in modes.items()}
        if any(len(set(v))!=len(v) for v in trials.values()) or set(trials["pod"])!=set(trials["host"]): continue
        means={mode:sum(r["bitsPerSecond"] for r in samples)/len(samples) for mode,samples in modes.items()}
        rows.append({**json.loads(key),"podBitsPerSecond":means["pod"],"hostBitsPerSecond":means["host"],
                     "hostOverPod":means["host"]/means["pod"],"pairs":len(trials["pod"])})
    return rows

def main():
    p=argparse.ArgumentParser();p.add_argument("directory");p.add_argument("--output",default="comparison-report.md")
    a=p.parse_args();records=[]
    for path in pathlib.Path(a.directory).glob("*.json"):
        try:
            obj=json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj,dict): records.append(obj)
        except (ValueError,UnicodeError): continue
    rows=comparisons(records)
    text=["# 实测对比报告","","数据目录："+str(pathlib.Path(a.directory).resolve()),"",
      "仅比较镜像及实际镜像摘要、网络模式、TCP 网段、参数、精度、资源限制和 Worker 节点列表一致的 MPI 结果。speedup < 1 表示本实现较慢。","","## 功能结果","",
      "| 用例 | 结果 | 证据 |","|---|---|---|"]
    for record in records:
        if "case" in record: text.append(f"| {record['case']} | {record.get('status','UNKNOWN')} | {record.get('evidence','')} |")
    text+=["","## 通信对照","","| 操作 | 限额 | 基线秒 | 本实现秒 | 加速比 | 样本对数 |","|---|---|---:|---:|---:|---|"]
    for r in rows: text.append(f"| {r['operation']} | {r['limited']} | {r['baseline_seconds']:.6g} | {r['candidate_seconds']:.6g} | {r['speedup']:.4f} | {r['samples']} |")
    if not rows: text.append("| 无可配对实测数据 | — | — | — | — | — |")
    text+=["","## 网络吞吐对照",""]
    network_rows=network_comparisons(records)
    for row in network_rows:
        text.append(f"- {row['clientNode']} → {row['serverNode']}，{row['comparisonId']}：普通 Pod TCP {row['podBitsPerSecond']/1e9:.4f} Gbit/s，hostNetwork TCP {row['hostBitsPerSecond']/1e9:.4f} Gbit/s，比值 {row['hostOverPod']:.4f}，{row['pairs']} 对样本。")
    if not network_rows: text.append("没有完整且条件一致的真实网络对照数据。")
    text+=["","## 大规模与时延",""]
    for record in records:
        if record.get("operation")=="scale":
            text.append(f"- 规模模式 {record['mode']}：{record['readyPods']}/{record['requestedPods']} Pods Ready，{len(record['observedWorkerNodes'])} 个执行节点，用时 {record['totalSeconds']:.3f} 秒。")
        if record.get("operation")=="tcp-echo":
            text.append(f"- TCP 实测 P99={record['p99_ms']:.3f}ms，最大={record['max_ms']:.3f}ms，截止期违约数={record['deadline_misses']}。")
        if record.get("operation")=="compute-deadline":
            text.append(f"- 周期计算 P99={record['p99_ns']/1e6:.3f}ms，最大={record['max_ns']/1e6:.3f}ms，截止期违约数={record['deadline_misses']}，CPU={record['cpu']}，FIFO={record['fifoPriority']}。")
    text+=["","未运行的项目保持“未实测”；功能通过不等于性能指标通过。普通 Linux/K8s 不提供硬实时保证。",
           "该报告不自动认定项目验收：需把实际节点/容器规模、吞吐门槛、截止期和允许违约数与正式验收阈值比较。",""]
    pathlib.Path(a.output).write_text("\n".join(text),encoding="utf-8")
    print(a.output)
if __name__=="__main__": main()

