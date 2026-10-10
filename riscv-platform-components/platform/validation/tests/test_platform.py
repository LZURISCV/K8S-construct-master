import copy
import importlib.util
import json
import math
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"control"));sys.path.insert(0,str(ROOT/"validation"))
from common import quantity, scale_decision, metric_sample, OWNER, FINALIZER, qualified
from resource import KubernetesBackend, Resources, resources
from scenario import Scenarios, Autoscaler, decide
from network import Networks, workload_pod, ports
from manifests import manifests
from report import comparisons, network_comparisons
from network_compare import validate_endpoints
from mpi_case import validate_ranks
from job import job
from launch import tcp_components
from latency import percentile

def obj(kind,spec,name="test"):
    return {"apiVersion":"platform.riscv.io/v1alpha1","kind":kind,
      "metadata":{"name":name,"namespace":"n","uid":"uid-123","resourceVersion":"1","generation":1},"spec":spec}

class Fake:
    def __init__(self):self.objects={};self.created=[];self.patches=[];self.statuses=[]
    def get(self,kind,name,ns=None,missing=False):
        value=self.objects.get((kind,name))
        if value is None and not missing: raise ValueError("missing "+kind+"/"+name)
        return copy.deepcopy(value)
    def items(self,kind,ns=None):return [copy.deepcopy(v) for (k,_),v in self.objects.items() if k==kind]
    def create(self,value):
        out=copy.deepcopy(value);out["metadata"].update(uid="new-uid",resourceVersion="1")
        kind={"Deployment":"deployment","Pod":"pod","NodeMigration":"nodemigration"}[out["kind"]]
        self.objects[kind,out["metadata"]["name"]]=out;self.created.append(out);return out
    def patch(self,kind,value,changes):
        result={**copy.deepcopy(value),**copy.deepcopy(changes)};self.patches.append((kind,result))
        self.objects[kind,value["metadata"]["name"]]=result;return result
    def status(self,value,status):self.statuses.append(status)
    def delete(self,kind,value):del self.objects[kind,value["metadata"]["name"]]

class PlatformTests(unittest.TestCase):
    def test_quantities(self):
        self.assertEqual(quantity("128Mi"),134217728)
        self.assertEqual(quantity("250m"),quantity("0.25"))
        with self.assertRaises(ValueError):quantity("-1")
    def test_claim_api_avoids_native_dra_name_collision(self):
        self.assertEqual(qualified("resourceclaims"),"resourceclaims.platform.riscv.io")
        self.assertEqual(qualified("resourceclaim"),"resourceclaims.platform.riscv.io")
        self.assertEqual(qualified("pods"),"pods")
    def test_scale_bounds(self):
        self.assertEqual(scale_decision(2,.9,.5,1,3),3)
        self.assertEqual(scale_decision(2,.51,.5,1,3),2)
        with self.assertRaises(ValueError):scale_decision(2,1,0,1,3)
    def test_metric_missing_stale_nan(self):
        for sample in ([],[{"value":[1,".9"]}],[{"value":[100,"NaN"]}]):
            with self.assertRaises(ValueError):metric_sample(sample,100,20)
    def test_metric_ambiguous(self):
        with self.assertRaises(ValueError):metric_sample([{"value":[100,"1"]}]*2,100)
    def test_cpu_memory_bounds(self):
        with self.assertRaises(ValueError):resources({"requests":{"cpu":"2"},"limits":{"cpu":"1"}})
    def test_device_resource_integer(self):
        with self.assertRaises(ValueError):resources({"requests":{"hardware.riscv.io/dev":".5"},"limits":{"hardware.riscv.io/dev":".5"}})
        self.assertEqual(resources({"requests":{"hardware.riscv.io/dev":"1"},"limits":{"hardware.riscv.io/dev":"1"}})["limits"]["hardware.riscv.io/dev"],"1")
    def claim(self):
        return obj("ResourceClaim",{"image":"registry/test@sha256:abc","replicas":1,"requests":{"cpu":"1","memory":"64Mi"},"limits":{"cpu":"2","memory":"128Mi"}})
    def test_allocate_scheduler_accounting(self):
        k=Fake();d=KubernetesBackend(k).allocate(self.claim())
        self.assertEqual(d["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"],"1")
        self.assertNotIn("nodeName",d["spec"]["template"]["spec"])
    def test_allocate_foreign_owner(self):
        k=Fake();d=KubernetesBackend(k).desired(self.claim());d["metadata"].update(uid="x",resourceVersion="1",labels={})
        k.objects["deployment","test"]=d
        with self.assertRaises(ValueError):KubernetesBackend(k).allocate(self.claim())
    def test_external_scaling_preserved(self):
        k=Fake();claim=self.claim();backend=KubernetesBackend(k);d=backend.allocate(claim);d["spec"]["replicas"]=4
        claim["spec"]["externalScaling"]=True
        self.assertEqual(backend.allocate(claim)["spec"]["replicas"],4)
    def test_no_node_name_bypass(self):
        claim=self.claim();claim["spec"]["nodeName"]="worker"
        with self.assertRaises(ValueError):KubernetesBackend(Fake()).desired(claim)
    def test_scene_hysteresis(self):
        s={"low":.2,"high":.8,"consecutiveSamples":2,"cooldownSeconds":30}
        self.assertEqual(decide(s,{},.9,100),( "high",1,False))
        self.assertTrue(decide(s,{"direction":"high","consecutive":1},.9,100)[2])
        self.assertFalse(decide(s,{"direction":"high","consecutive":1,"lastActionEpoch":90},.9,100)[2])
        self.assertEqual(decide(s,{"direction":"high","consecutive":2},.5,100),("hold",0,False))
    def scene_setup(self):
        k=Fake();d={"metadata":{"name":"web","namespace":"n","uid":"dep","resourceVersion":"1",
          "annotations":{"platform.riscv.io/allow-scaling":"test"}},"spec":{"replicas":1}}
        k.objects["deployment","web"]=d
        policy=obj("ScenarioPolicy",{"query":"q","low":.2,"high":.8,"consecutiveSamples":1,"cooldownSeconds":0,
            "maxReplicas":3,"highAction":{"type":"scale","deployment":"web","delta":1}})
        return k,policy
    def test_scene_real_action_and_bound(self):
        k,p=self.scene_setup();s=Scenarios(k,{"prometheusURL":"http://p"},lambda *_:[{"value":[100,".9"]}],lambda:100)
        s.step(p);self.assertEqual(k.patches[-1][1]["spec"]["replicas"],2)
    def test_scene_optin_required(self):
        k,p=self.scene_setup();k.objects["deployment","web"]["metadata"]["annotations"]={}
        with self.assertRaises(ValueError):Scenarios(k,{"prometheusURL":"p"},lambda *_:[{"value":[100,".9"]}],lambda:100).step(p)
    def test_scene_hpa_conflict(self):
        k,p=self.scene_setup();k.objects["horizontalpodautoscalers","h"]={"spec":{"scaleTargetRef":{"name":"web"}}}
        with self.assertRaises(ValueError):Scenarios(k,{"prometheusURL":"p"},lambda *_:[{"value":[100,".9"]}],lambda:100).step(p)
    def test_duplicate_scrape_not_counted(self):
        k,p=self.scene_setup();p["status"]={"lastSampleEpoch":100}
        Scenarios(k,{"prometheusURL":"p"},lambda *_:[{"value":[100,".9"]}],lambda:100).step(p)
        self.assertEqual(k.patches,[]);self.assertEqual(k.statuses,[])
    def test_scale_recovery_does_not_apply_delta_twice(self):
        k,p=self.scene_setup()
        original=k.status
        def fail_commit(value,status):
            if status.get("pendingScale") is None: raise RuntimeError("status commit interrupted")
            original(value,status)
        k.status=fail_commit
        scene=Scenarios(k,{"prometheusURL":"p"},lambda *_:[{"value":[100,".9"]}],lambda:100)
        with self.assertRaises(RuntimeError):scene.step(p)
        self.assertEqual(k.objects["deployment","web"]["spec"]["replicas"],2)
        p["status"]=k.statuses[0];k.status=original
        scene.step(p)
        self.assertEqual(k.objects["deployment","web"]["spec"]["replicas"],2)
        self.assertEqual(len(k.patches),1)
    def test_scale_recovery_rejects_name_reuse(self):
        k,p=self.scene_setup()
        p["status"]={"pendingScale":{"workload":"web","workloadUID":"old","from":1,"to":2}}
        with self.assertRaises(ValueError):Scenarios(k,{},clock=lambda:100).step(p)
        self.assertEqual(k.patches,[])
    def test_old_rollout_is_not_ready(self):
        k=Fake();claim=self.claim();backend=KubernetesBackend(k);d=backend.allocate(claim)
        d["metadata"]["generation"]=2
        d["status"]={"observedGeneration":1,"availableReplicas":1,"updatedReplicas":0}
        Resources(k).claim(claim)
        self.assertEqual(k.statuses[-1]["phase"],"Allocating")
    def test_autoscale_low_window(self):
        k,p=self.scene_setup();k.objects["deployment","web"]["spec"]["replicas"]=2
        p=obj("ResourcePolicy",{"deployment":"web","query":"q","target":.5,"minReplicas":1,"maxReplicas":3,
          "downscaleStabilizationSeconds":60,"cooldownSeconds":0})
        clock=[100]
        scale=Autoscaler(k,{"prometheusURL":"p"},lambda *_:[{"value":[clock[0],".1"]}],lambda:clock[0])
        scale.step(p);self.assertEqual(k.patches,[])
        p["status"]=k.statuses[-1];clock[0]=130;scale.step(p);self.assertEqual(k.patches,[])
        p["status"]=k.statuses[-1];clock[0]=160;scale.step(p)
        self.assertEqual(k.patches[-1][1]["spec"]["replicas"],1)
    def test_profile_preserves_foreign_labels_taints(self):
        k=Fake();k.objects["node","w"]={"metadata":{"name":"w","uid":"node","resourceVersion":"1","labels":{"keep":"yes"}},
              "spec":{"taints":[{"key":"foreign","effect":"NoSchedule"}]},"status":{"allocatable":{"cpu":"4"}}}
        p=obj("NodeProfile",{"node":"w","labels":{"platform.riscv.io/pool":"compute"},"taints":[]})
        k.objects["nodeprofiles","test"]=p;Resources(k).profile(p)
        self.assertEqual(k.patches[0][1]["metadata"]["labels"]["keep"],"yes")
        self.assertEqual(k.patches[0][1]["spec"]["taints"][0]["key"],"foreign")
    def test_profile_rejects_foreign_key(self):
        k=Fake();k.objects["node","w"]={"metadata":{},"spec":{}}
        with self.assertRaises(ValueError):Resources(k).profile(obj("NodeProfile",{"node":"w","labels":{"kubernetes.io/arch":"fake"}}))
    def network_setup(self):
        k=Fake();p=obj("HostNetworkWorkload",{"node":"w","image":"tools","ports":[{"containerPort":25201}],
           "requests":{"cpu":"1","memory":"64Mi"},"limits":{"cpu":"1","memory":"64Mi"}})
        p["metadata"]["finalizers"]=[FINALIZER]
        k.objects["hostnetworkworkloads","test"]=p
        k.objects["node","w"]={"metadata":{"name":"w","labels":{"kubernetes.io/hostname":"w"}},"spec":{},
          "status":{"conditions":[{"type":"Ready","status":"True"}]}}
        return k,p
    def test_network_host_scheduler_accounting(self):
        k,p=self.network_setup();Networks(k).step(p)
        pod=k.created[0];self.assertEqual(pod["kind"],"Pod")
        self.assertTrue(pod["spec"]["hostNetwork"])
        self.assertEqual(pod["spec"]["dnsPolicy"],"ClusterFirstWithHostNet")
        self.assertEqual(pod["spec"]["containers"][0]["ports"][0]["hostPort"],25201)
        self.assertNotIn("nodeName",pod["spec"])
    def test_network_collision_across_namespaces(self):
        k,p=self.network_setup();peer=copy.deepcopy(p);peer["metadata"].update(uid="other",namespace="other")
        k.objects["hostnetworkworkloads","other"]=peer
        with self.assertRaises(ValueError):Networks(k).step(p)
        self.assertEqual(k.created,[])
    def test_network_distinct_ports_allowed(self):
        k,p=self.network_setup();peer=copy.deepcopy(p);peer["metadata"]["uid"]="other"
        peer["spec"]["ports"]=[{"containerPort":25202}];k.objects["hostnetworkworkloads","other"]=peer
        Networks(k).step(p);self.assertEqual(len(k.created),1)
    def test_network_legacy_spec_rejected(self):
        k,p=self.network_setup();p["spec"]["device"]="eth1"
        with self.assertRaises(ValueError):Networks(k).step(p)
    def test_network_port_validation(self):
        for value in ([{"containerPort":22}],[{"containerPort":True}],[{"containerPort":25201,"protocol":"UDP"}],
                      [{"containerPort":25201}]*2,[{"containerPort":25201,"hostPort":25202}]):
            with self.assertRaises(ValueError):ports({"ports":value})
    def test_network_outgoing_only(self):
        k,p=self.network_setup();p["spec"]["ports"]=[]
        self.assertEqual(workload_pod(p)["spec"]["containers"][0]["ports"],[])
    def test_network_foreign_pod_refused(self):
        k,p=self.network_setup();pod=workload_pod(p);pod["metadata"]["labels"]={}
        k.objects["pod",pod["metadata"]["name"]]=pod
        with self.assertRaises(ValueError):Networks(k).step(p)
    def test_network_spec_immutable(self):
        k,p=self.network_setup();Networks(k).step(p);p["spec"]["image"]="changed"
        with self.assertRaises(ValueError):Networks(k).step(p)
    def test_network_delete_waits_then_releases(self):
        k,p=self.network_setup();Networks(k).step(p)
        p["metadata"]["deletionTimestamp"]="now";Networks(k).step(p)
        self.assertEqual(k.statuses[-1]["phase"],"Releasing");self.assertEqual(k.patches,[])
        Networks(k).step(p);self.assertEqual(k.patches[-1][1]["metadata"]["finalizers"],[])
    def test_network_unready_delete_retains_reservation(self):
        k,p=self.network_setup();p["metadata"]["deletionTimestamp"]="now"
        k.objects["node","w"]["status"]["conditions"][0]["status"]="False"
        Networks(k).step(p);self.assertEqual(k.patches,[])
        self.assertEqual(k.statuses[-1]["phase"],"Releasing")
    def test_host_mpi_requires_network_and_avoids_ssh22(self):
        with self.assertRaises(ValueError):job("b","n","img","bcast","native")
        result=job("b","n","img","bcast","native",tcp_network="192.168.102.0/24")
        worker=result["spec"]["mpiReplicaSpecs"]["Worker"]["template"]["spec"]
        self.assertTrue(worker["hostNetwork"]);self.assertIn("affinity",worker)
        self.assertEqual(worker["containers"][0]["ports"][0]["hostPort"],25222)
        self.assertIn("RV_WORKER_POD",worker["containers"][0]["command"][-1])
        self.assertEqual(result["spec"]["launcherCreationPolicy"],"WaitForWorkersReady")
    def test_pod_mpi_baseline_no_host_port(self):
        result=job("b","n","img","bcast","native",network="pod")
        worker=result["spec"]["mpiReplicaSpecs"]["Worker"]["template"]["spec"]
        self.assertNotIn("hostNetwork",worker);self.assertNotIn("hostPort",worker["containers"][0]["ports"][0])
    def test_mpi_transport_override_rejected(self):
        with self.assertRaises(ValueError):job("b","n","img","bcast","native",network="pod",mpi_args=["--mca","pml","ucx"])
    def test_tcp_transport_keeps_shared_memory(self):
        self.assertEqual(tcp_components("mca:btl:vader:version:mca:2"),"self,tcp,vader")
        self.assertEqual(tcp_components("mca:btl:sm:version:mca:2"),"self,tcp,sm")
        self.assertEqual(tcp_components(""),"self,tcp")
    def test_mpi_real_worker_identity(self):
        members=[{"metadata":{"name":"b-worker-"+str(i)},"spec":{"nodeName":"node"+str(i)}} for i in range(2)]
        sample={"ranks":4,"worker_pod_names":["b-worker-0"]*2+["b-worker-1"]*2}
        self.assertEqual(validate_ranks(sample,members,2,2,True),["node0","node1"])
        sample["worker_pod_names"]=["launcher"]*4
        with self.assertRaises(AssertionError):validate_ranks(sample,members,2,2,True)
    def test_network_actual_endpoint_validation(self):
        def endpoint(node,ip):
            return {"spec":{"nodeName":node,"hostNetwork":True},"status":{"podIP":ip,"hostIP":ip,
              "conditions":[{"type":"Ready","status":"True"}]}}
        left,right=endpoint("a","192.168.1.1"),endpoint("b","192.168.1.2")
        self.assertEqual(validate_endpoints(left,right,"host","a","b"),"192.168.1.2")
        with self.assertRaises(ValueError):validate_endpoints(left,right,"pod","a","b")
        with self.assertRaises(ValueError):validate_endpoints(left,right,"host","a","a")
    def network_records(self):
        base={"operation":"iperf3","comparisonId":"run1","clientNode":"a","serverNode":"b",
              "image":"img","imageID":"sha256:one","seconds":30,"streams":4,"port":25201,
              "resources":{"cpu":"1"},"trial":0,"evidence":"real-iperf3"}
        return [{**base,"mode":"pod","hostNetwork":False,"bitsPerSecond":100},
                {**base,"mode":"host","hostNetwork":True,"bitsPerSecond":80}]
    def test_network_report_preserves_slowdown(self):
        self.assertEqual(network_comparisons(self.network_records())[0]["hostOverPod"],.8)
    def test_network_report_rejects_mismatched_evidence(self):
        for key,value in (("comparisonId","other"),("imageID","different"),("trial",1),("serverNode","a"),
                          ("hostNetwork",False),("bitsPerSecond",float("nan")),("evidence","simulated")):
            records=self.network_records();records[1][key]=value
            self.assertEqual(network_comparisons(records),[],key)
    def test_network_report_rejects_duplicate_trials(self):
        records=self.network_records();self.assertEqual(network_comparisons(records+[records[0]]),[])
    def test_report_mismatched_workers_not_compared(self):
        base={"operation":"bcast","mode":"native","mean_seconds":2,"correct":True,"evidence":"real-MPIJob","workerNodes":["a"]}
        other={**base,"mode":"hierarchical","mean_seconds":1,"workerNodes":["b"]}
        self.assertEqual(comparisons([base,other]),[])
        other["workerNodes"]=["a"]
        self.assertEqual(comparisons([base,other])[0]["speedup"],2)
    def test_report_bad_result_not_compared(self):
        base={"operation":"fusion","mode":"serial","mean_seconds":2,"correct":True,"evidence":"real-MPIJob"}
        self.assertEqual(comparisons([base,{**base,"mode":"overlap","mean_seconds":1,"correct":False}]),[])
    def test_manifest_has_status_subresources(self):
        crds=[o for o in manifests()["items"] if o["kind"]=="CustomResourceDefinition"]
        self.assertEqual(len(crds),5)
        self.assertIn('hostnetworkworkloads.platform.riscv.io',{o['metadata']['name'] for o in crds})
        self.assertNotIn('directworkloads.platform.riscv.io',{o['metadata']['name'] for o in crds})
        self.assertEqual(crds[0]["spec"]["versions"][0]["subresources"],{"status":{}})
    def test_percentile_tail(self):
        self.assertEqual(percentile([4,2,1,3],.99),4)
if __name__=="__main__":unittest.main()

