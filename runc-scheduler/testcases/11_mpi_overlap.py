#!/usr/bin/env python3
from common import require, run
from mpi_common import MPI, mpi_parser

def test(c):
    require(0 < c.args.elements <= 16777216 and c.args.loops > 0 and 0 < c.args.rounds <= 100,
            "invalid measurement sizes")
    require(c.args.min_speedup > 0, "speedup threshold must be positive")
    mpi = MPI(c)
    baseline_limits = mpi.workers()
    baseline = mpi.job("overlap", "baseline")
    limited_limits = mpi.workers(limited=True)
    limited = mpi.job("overlap", "limited")
    met = baseline["speedup"] >= c.args.min_speedup
    slowed = limited["overlapSeconds"] > baseline["overlapSeconds"]
    return {"status": "PASS" if met and slowed else "PERFORMANCE_NOT_MET", "results": mpi.measurements,
            "baselineCPULimits": baseline_limits, "limitedCPULimits": limited_limits,
            "minSpeedup": c.args.min_speedup, "overlapThresholdMet": met, "limitSlowdownObserved": slowed,
            "communicationScope": "cross-device" if len(c.nodes) > 1 else "single-device multiple processes"}

if __name__ == "__main__":
    p = mpi_parser("3-2-0011", "MPI compute/communication overlap and CPU limit comparison")
    p.add_argument("--elements", type=int, default=1048576)
    p.add_argument("--loops", type=int, default=2000000)
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--min-speedup", type=float, default=1.05)
    raise SystemExit(run("3-2-0011", p.parse_args(), test, image="mpi"))
