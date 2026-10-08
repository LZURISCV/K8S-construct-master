package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

var testNow = time.Date(2026, 10, 6, 0, 0, 0, 0, time.UTC)

func testEngine(t *testing.T) *Engine {
	t.Helper()
	e, err := newEngine("", 30*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	return e
}
func testNode(id string, cpu int64) Node {
	return Node{ID: id, Address: "192.0.2.10", Architecture: "riscv64", Capacity: Resources{cpu, 1 << 30}, Images: []string{"demo"}, Labels: Selector{"zone": "zone1"}}
}
func addNode(t *testing.T, e *Engine, id string, cpu int64) {
	t.Helper()
	if _, err := e.heartbeat(Heartbeat{Node: testNode(id, cpu)}, testNow); err != nil {
		t.Fatal(err)
	}
}
func testWorkload(name string, replicas int, cpu int64) Workload {
	return Workload{Name: name, Kind: "deployment", Replicas: replicas, Template: Template{Image: "demo", Command: []string{"/bin/sleep", "3600"}, Requests: Resources{cpu, 64 << 20}, Limits: Resources{cpu, 128 << 20}, Labels: Selector{"app": name}}}
}
func applyTest(t *testing.T, e *Engine, w Workload) {
	t.Helper()
	if err := e.apply(w, testNow); err != nil {
		t.Fatal(err)
	}
}
func podOf(t *testing.T, s State, name string, phase string) *Pod {
	t.Helper()
	for _, p := range s.Pods {
		if p.Workload == name && (phase == "" || p.Phase == phase) {
			return p
		}
	}
	t.Fatalf("no pod for %s phase %s", name, phase)
	return nil
}
func reportRunning(t *testing.T, e *Engine, id string, now time.Time, mem int64) {
	t.Helper()
	s := e.snapshot()
	reports := []PodReport{}
	for _, p := range s.Pods {
		if p.NodeID == id && !terminal(p) {
			reports = append(reports, PodReport{ID: p.ID, Phase: "Running", MemoryUsageBytes: mem, CPUUsageMillis: 100, MetricsValid: true})
		}
	}
	if _, err := e.heartbeat(Heartbeat{Node: testNode(id, s.Nodes[id].Capacity.CPUMillis), Pods: reports}, now); err != nil {
		t.Fatal(err)
	}
}
func TestResourceReservationsAndBalance(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 1000)
	addNode(t, e, "worker-b", 1000)
	applyTest(t, e, testWorkload("balanced", 3, 800))
	s := e.snapshot()
	nodes := map[string]int{}
	pending := 0
	for _, p := range s.Pods {
		if p.Phase == "Assigned" {
			nodes[p.NodeID]++
		} else if p.Phase == "Pending" {
			pending++
		}
	}
	if nodes["worker-a"] != 1 || nodes["worker-b"] != 1 || pending != 1 {
		t.Fatalf("oversubscription or imbalance: %+v pending=%d", nodes, pending)
	}
}
func TestRequiredNodeAffinityAndMissingImage(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 2000)
	w := testWorkload("blocked", 1, 100)
	w.Template.NodeAffinity = []NodeTerm{{MatchExpressions: []Requirement{{"zone", "In", []string{"zone2"}}}}}
	applyTest(t, e, w)
	if podOf(t, e.snapshot(), "blocked", "").Phase != "Pending" {
		t.Fatal("affinity ignored")
	}
	zone := "zone2"
	if err := e.patchNode("worker-a", NodePatch{Labels: map[string]*string{"zone": &zone}}, testNow); err != nil {
		t.Fatal(err)
	}
	podOf(t, e.snapshot(), "blocked", "Assigned")
	w = testWorkload("image-missing", 1, 100)
	w.Template.Image = "absent"
	applyTest(t, e, w)
	podOf(t, e.snapshot(), w.Name, "Pending")
}
func TestPodAffinityAndAntiAffinityBothDirections(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 3000)
	addNode(t, e, "worker-b", 3000)
	anchor := testWorkload("anchor", 1, 100)
	applyTest(t, e, anchor)
	first := podOf(t, e.snapshot(), "anchor", "")
	follower := testWorkload("follower", 1, 100)
	follower.Template.PodAffinity = []PodAffinity{{Selector{"app": "anchor"}, "rcs.node"}}
	applyTest(t, e, follower)
	if podOf(t, e.snapshot(), "follower", "").NodeID != first.NodeID {
		t.Fatal("pod affinity failed")
	}
	spread := testWorkload("spread", 2, 100)
	spread.Template.PodAntiAffinity = []PodAffinity{{Selector{"app": "spread"}, "rcs.node"}}
	applyTest(t, e, spread)
	seen := map[string]bool{}
	for _, p := range e.snapshot().Pods {
		if p.Workload == "spread" {
			if p.NodeID == "" || seen[p.NodeID] {
				t.Fatal("anti-affinity failed")
			}
			seen[p.NodeID] = true
		}
	}
	third := testWorkload("spread", 3, 100)
	third.Template = spread.Template
	applyTest(t, e, third)
	pending := 0
	for _, p := range e.snapshot().Pods {
		if p.Workload == "spread" && p.Phase == "Pending" {
			pending++
		}
	}
	if pending != 1 {
		t.Fatal("third anti-affinity replica must stay pending")
	}
}
func TestTaintsAndNoExecute(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 2000)
	ts := []Taint{{"dedicated", "test", "NoSchedule"}}
	if err := e.patchNode("worker-a", NodePatch{Taints: &ts}, testNow); err != nil {
		t.Fatal(err)
	}
	applyTest(t, e, testWorkload("blocked", 1, 100))
	podOf(t, e.snapshot(), "blocked", "Pending")
	w := testWorkload("allowed", 1, 100)
	w.Template.Tolerations = []Toleration{{"dedicated", "test", "Equal", "NoSchedule"}}
	applyTest(t, e, w)
	p := podOf(t, e.snapshot(), "allowed", "Assigned")
	ts = []Taint{{"dedicated", "test", "NoExecute"}}
	_ = e.patchNode("worker-a", NodePatch{Taints: &ts}, testNow)
	if e.snapshot().Pods[p.ID].Phase != "Terminating" {
		t.Fatal("NoExecute did not evict")
	}
}
func TestPreemptionWaitsForPhysicalRelease(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 1000)
	low := testWorkload("low", 1, 800)
	low.Template.Priority = 10
	applyTest(t, e, low)
	lp := podOf(t, e.snapshot(), "low", "")
	high := testWorkload("high", 1, 800)
	high.Template.Priority = 100
	applyTest(t, e, high)
	s := e.snapshot()
	hp := podOf(t, s, "high", "")
	if s.Pods[lp.ID].Phase != "Terminating" || hp.Phase != "Pending" {
		t.Fatal("new pod bound before preempted container stopped")
	}
	_, err := e.heartbeat(Heartbeat{Node: testNode("worker-a", 1000), Pods: []PodReport{{ID: lp.ID, Phase: "Running"}}}, testNow.Add(time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if e.snapshot().Pods[hp.ID].Phase != "Pending" {
		t.Fatal("running victim incorrectly released")
	}
	_, err = e.heartbeat(Heartbeat{Node: testNode("worker-a", 1000)}, testNow.Add(2*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	s = e.snapshot()
	if s.Pods[hp.ID].Phase != "Assigned" || s.Pods[lp.ID].Phase != "Removed" {
		t.Fatal("high priority not assigned after stop acknowledgement")
	}
	for _, p := range s.Pods {
		if p.Workload == "low" && p.ID != lp.ID && p.Phase != "Pending" {
			t.Fatal("low replacement stole reserved resources")
		}
	}
}
func TestOfflineNodeRetainsReservations(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 2000)
	addNode(t, e, "worker-b", 2000)
	applyTest(t, e, testWorkload("singleton", 1, 100))
	p := podOf(t, e.snapshot(), "singleton", "")
	if err := e.tick(testNow.Add(40 * time.Second)); err != nil {
		t.Fatal(err)
	}
	s := e.snapshot()
	if s.Nodes[p.NodeID].Ready || len(s.Pods) != 1 || !consumes(s.Pods[p.ID]) {
		t.Fatal("offline node must not produce duplicate containers")
	}
}
func TestAutoscaleAndStabilization(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 10000)
	w := testWorkload("elastic", 1, 100)
	w.Autoscaler = &Autoscaler{Metric: "memory", TargetPercent: 50, MinReplicas: 1, MaxReplicas: 3, ScaleDownWindowSeconds: 20, CooldownSeconds: 1}
	applyTest(t, e, w)
	reportRunning(t, e, "worker-a", testNow.Add(2*time.Second), 64<<20)
	if e.snapshot().Workloads[w.Name].Replicas != 2 {
		t.Fatal("scale up failed")
	}
	reportRunning(t, e, "worker-a", testNow.Add(3*time.Second), 64<<20)
	if e.snapshot().Workloads[w.Name].Replicas != 3 {
		t.Fatal("max replica clamp failed")
	}
	reportRunning(t, e, "worker-a", testNow.Add(4*time.Second), 1<<20)
	if e.snapshot().Workloads[w.Name].Replicas != 3 {
		t.Fatal("scale down stabilization ignored")
	}
	reportRunning(t, e, "worker-a", testNow.Add(25*time.Second), 1<<20)
	if e.snapshot().Workloads[w.Name].Replicas != 1 {
		t.Fatal("scale down failed after window")
	}
}
func TestJobSuccessIsNotRestartedAndExitCodeValidated(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 2000)
	w := testWorkload("job", 1, 100)
	w.Kind = "job"
	applyTest(t, e, w)
	p := podOf(t, e.snapshot(), "job", "")
	if _, err := e.heartbeat(Heartbeat{Node: testNode("worker-a", 2000), Pods: []PodReport{{ID: p.ID, Phase: "Succeeded"}}}, testNow); err == nil {
		t.Fatal("success without exit code accepted")
	}
	zero := 0
	if _, err := e.heartbeat(Heartbeat{Node: testNode("worker-a", 2000), Pods: []PodReport{{ID: p.ID, Phase: "Succeeded", ExitCode: &zero, Log: "PASS"}}}, testNow); err != nil {
		t.Fatal(err)
	}
	if len(e.snapshot().Pods) != 1 {
		t.Fatal("completed job relaunched")
	}
}
func TestControllerPersistsAndRollsBackOnFailure(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")
	e, err := newEngine(path, 30*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	addNode(t, e, "worker-a", 2000)
	applyTest(t, e, testWorkload("durable", 1, 100))
	reloaded, err := newEngine(path, 30*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(reloaded.snapshot().Pods) != 1 {
		t.Fatal("state lost")
	}
	old, _ := json.Marshal(e.snapshot())
	e.path = dir
	err = e.apply(testWorkload("must-rollback", 1, 100), testNow)
	if err == nil {
		t.Fatal("persist should fail replacing a directory")
	}
	after, _ := json.Marshal(e.snapshot())
	if string(old) != string(after) {
		t.Fatal("in-memory state not rolled back")
	}
	if _, err = os.Stat(path); err != nil {
		t.Fatal(err)
	}
}
func TestWorkloadUpdateAndDeletion(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 1000)
	w := testWorkload("rollout", 1, 800)
	applyTest(t, e, w)
	old := podOf(t, e.snapshot(), w.Name, "")
	w.Template.Command = []string{"/bin/echo", "updated"}
	applyTest(t, e, w)
	s := e.snapshot()
	if s.Pods[old.ID].Phase != "Terminating" {
		t.Fatal("old generation not stopped")
	}
	p := podOf(t, s, w.Name, "Pending")
	if p.Generation != 2 {
		t.Fatal("generation not incremented")
	}
	if err := e.remove(w.Name, testNow); err != nil {
		t.Fatal(err)
	}
	if e.snapshot().Pods[p.ID].Phase != "Removed" {
		t.Fatal("pending pod not deleted")
	}
}
