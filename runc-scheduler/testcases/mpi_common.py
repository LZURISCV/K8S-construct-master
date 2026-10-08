"""MPI deployment helpers; each numbered script chooses one MPI program."""
import json
import re
from common import parser, pods, require

def mpi_parser(case_id, title):
    p = parser(case_id, title)
    p.add_argument("--ranks-per-node", type=int, default=2)
    p.add_argument("--interface", default="", help="MPI TCP interface or subnet CIDR")
    p.add_argument("--job-timeout", type=int, default=900)
    return p

class MPI:
    def __init__(self, c):
        self.c = c
        self.nodes = [c.initial["nodes"][n] for n in c.nodes]
        require(c.args.ranks_per_node >= 2, "use at least two ranks per node")
        require(c.args.job_timeout > 0, "job timeout must be positive")
        require(len({n["architecture"] for n in self.nodes}) == 1, "MPI rootfs architectures must match")
        require(all(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", n["address"]) for n in self.nodes), "IPv4 addresses required")
        require(len({n["address"] for n in self.nodes}) == len(self.nodes), "distinct worker addresses required")
        self.measurements = []

    def base(self, node, cpu):
        return {"image": "mpi", "nodeSelector": {"rcs.node": node},
                "requests": {"cpuMillis": cpu, "memoryBytes": 256 * 1024**2},
                "limits": {"cpuMillis": cpu, "memoryBytes": 512 * 1024**2}}

    def workers(self, limited=False):
        limits = {}
        for i, node in enumerate(self.nodes):
            cpu = min(2000, node["capacity"]["cpuMillis"] - 250)
            if limited: cpu = max(100, cpu // 2)
            spec = self.base(node["id"], cpu)
            spec.update(command=["/usr/sbin/sshd", "-D", "-e"], hostPorts=[2222])
            self.c.apply("worker-" + str(i), spec=spec)
            limits[node["id"]] = cpu
        # running() filters the current generation after a CPU limit rollout.
        for i in range(len(self.nodes)): self.c.running(self.c.prefix + "-worker-" + str(i))
        return limits

    def job(self, mode, suffix=None):
        c = self.c
        spec = self.base(self.nodes[0]["id"], 100)
        spec.update(command=["/opt/rcs/mpi-launch.sh"], env={
            "RCS_MPI_HOSTS": ",".join(n["address"] for n in self.nodes),
            "RCS_MPI_RANKS": str(len(self.nodes) * c.args.ranks_per_node),
            "RCS_MPI_RANKS_PER_NODE": str(c.args.ranks_per_node), "RCS_MPI_TEST": mode,
            "RCS_MPI_ELEMENTS": str(getattr(c.args, "elements", 1048576)),
            "RCS_MPI_LOOPS": str(getattr(c.args, "loops", 2000000)),
            "RCS_MPI_ROUNDS": str(getattr(c.args, "rounds", 5)), "RCS_MPI_INTERFACE": c.args.interface})
        name = c.apply(suffix or mode, spec=spec, kind="job")
        def finished(s):
            generation = s["workloads"][name]["generation"]
            for pod in s["pods"].values():
                if pod["workload"] == name and pod["generation"] == generation:
                    if pod["phase"] == "Failed": raise RuntimeError(pod.get("reason", "") + "\n" + pod.get("log", ""))
                    if pod["phase"] == "Succeeded": return pod
            return None
        result = c.api.wait(finished, timeout=c.args.job_timeout, description=name + " completion")
        matches = re.findall(r"RCS_RESULT (\{[^\n]+\})", result.get("log", ""))
        require(matches, "no MPI result in launcher log")
        measurement = json.loads(matches[-1])
        require(measurement.get("test") == mode, "MPI launcher executed unexpected test")
        require(measurement.get("passed"), "MPI numerical validation failed")
        require(measurement.get("ranks") == len(self.nodes) * c.args.ranks_per_node, "unexpected rank count")
        evidence = {"job": name, "pod": result["id"], "measurement": measurement, "log": result.get("log", "")}
        self.measurements.append(evidence)
        return measurement

def collective(c, mode):
    mpi = MPI(c)
    limits = mpi.workers()
    mpi.job(mode)
    return {"workerCPULimits": limits, "results": mpi.measurements,
            "communicationScope": "cross-device" if len(c.nodes) > 1 else "single-device multiple processes"}
