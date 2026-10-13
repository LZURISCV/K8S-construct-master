#!/usr/bin/env python3
"""Check Prometheus scrape health and fresh real node samples from the CLI."""
import argparse
import json
import math
import time
import urllib.parse
import urllib.request

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with HTTP.open(request, timeout=10) as response:
        return json.load(response)


def query(url, expression):
    result = get(url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": expression}))
    if result.get("status") != "success" or result["data"]["resultType"] != "vector":
        raise RuntimeError("Prometheus instant query failed")
    samples = result["data"]["result"]
    if len(samples) != 1:
        raise RuntimeError("expected exactly one sample: " + expression)
    value = float(samples[0]["value"][1])
    if not math.isfinite(value):
        raise RuntimeError("non-finite metric: " + expression)
    return value


def verify(prometheus, expected_targets):
    targets = get(prometheus.rstrip("/") + "/api/v1/targets")["data"]["activeTargets"]
    nodes = [target for target in targets if target["labels"].get("job") == "rv-nodes"]
    present = {target["labels"].get("instance") for target in nodes}
    if not nodes or not set(expected_targets).issubset(present):
        raise RuntimeError("exporter targets missing: " + repr(sorted(set(expected_targets) - present)))
    failed = [(target["labels"].get("instance"), target.get("lastError", ""))
              for target in nodes if target["health"] != "up"]
    if failed:
        raise RuntimeError("exporter scrape failed: " + repr(failed))
    samples = []
    for target in nodes:
        instance = target["labels"]["instance"]
        selector = '{job="rv-nodes",instance=' + json.dumps(instance) + '}'
        cpu = query(prometheus, "rv_node_cpu_utilization_ratio" + selector)
        timestamp = query(prometheus, "rv_node_sample_timestamp_seconds" + selector)
        age = time.time() - timestamp
        if not 0 <= cpu <= 1 or not -10 <= age <= 75:
            raise RuntimeError("invalid CPU sample or stale exporter clock/sample: " + instance)
        samples.append({"instance": instance, "cpuRatio": cpu, "sampleAgeSeconds": round(age, 2)})
    return {"passed": True, "nodeSamples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus", default="http://192.168.102.125:30090")
    parser.add_argument("--target", action="append", help="expected exporter instance; repeat for more nodes")
    parser.add_argument("--attempts", type=int, default=12)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be positive")
    targets = args.target or ["192.168.102.125:9108"]
    for attempt in range(args.attempts):
        try:
            result = verify(args.prometheus, targets)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        except Exception as error:
            if attempt + 1 == args.attempts:
                raise SystemExit("Monitoring verification failed: " + str(error))
            print("Waiting for monitoring readiness: " + str(error), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
