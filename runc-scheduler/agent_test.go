package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

type fakeRuntime struct {
	running              map[string]bool
	channels             map[string]chan processResult
	starts, stops, execs int
	dir                  string
}

func (f *fakeRuntime) Start(p Pod) (<-chan processResult, error) {
	f.starts++
	f.running[p.ID] = true
	ch := make(chan processResult, 1)
	f.channels[p.ID] = ch
	return ch, nil
}
func (f *fakeRuntime) Inspect(id string) (string, error) {
	if f.running[id] {
		return "running", nil
	}
	return "absent", nil
}
func (f *fakeRuntime) Stop(id string) error {
	if f.running[id] {
		f.stops++
		f.running[id] = false
		f.channels[id] <- processResult{code: 143}
	}
	return nil
}
func (f *fakeRuntime) Stats(id string, m Metrics) Metrics {
	return Metrics{CPUUsageMillis: 50, MemoryUsageBytes: 1 << 20, Valid: true}
}
func (f *fakeRuntime) Exec(ctx context.Context, id string, args []string) (string, int) {
	f.execs++
	return "executed", 0
}
func (f *fakeRuntime) Logs(id string) string  { return "runtime log" }
func (f *fakeRuntime) Remove(id string) error { return os.RemoveAll(filepath.Join(f.dir, "pods", id)) }
func TestAgentStartsOnceReportsMetricsAndStopsOnRemoval(t *testing.T) {
	dir := t.TempDir()
	rt := &fakeRuntime{running: map[string]bool{}, channels: map[string]chan processResult{}, dir: dir}
	a := &Agent{config: AgentConfig{ID: "worker-a", DataDir: dir}, runtime: rt, pods: map[string]*localPod{}, results: map[string]Operation{}}
	p := Pod{ID: "rcs-000000000001", NodeID: "worker-a", Spec: testWorkload("test", 1, 100).Template}
	want := Assignment{Pods: []Pod{p}}
	if err := a.synchronize(context.Background(), want); err != nil {
		t.Fatal(err)
	}
	if err := a.synchronize(context.Background(), want); err != nil {
		t.Fatal(err)
	}
	reports, err := a.reports()
	if err != nil {
		t.Fatal(err)
	}
	if rt.starts != 1 || len(reports) != 1 || reports[0].Phase != "Running" || !reports[0].MetricsValid {
		t.Fatal("agent did not reconcile or report correctly")
	}
	op := Operation{ID: "op-1", PodID: p.ID, NodeID: "worker-a", Command: []string{"/bin/true"}, Status: "Pending"}
	want.Operations = []Operation{op}
	_ = a.synchronize(context.Background(), want)
	_ = a.synchronize(context.Background(), want)
	if rt.execs != 1 {
		t.Fatal("retried operation executed twice")
	}
	if err := a.synchronize(context.Background(), Assignment{}); err != nil {
		t.Fatal(err)
	}
	if rt.stops != 1 || len(a.pods) != 0 {
		t.Fatal("agent did not remove desired-absent pod")
	}
}
func TestAgentCapturesJobExitAndLog(t *testing.T) {
	dir := t.TempDir()
	rt := &fakeRuntime{running: map[string]bool{}, channels: map[string]chan processResult{}, dir: dir}
	a := &Agent{config: AgentConfig{ID: "worker-a", DataDir: dir}, runtime: rt, pods: map[string]*localPod{}, results: map[string]Operation{}}
	p := Pod{ID: "rcs-000000000001", NodeID: "worker-a"}
	if err := a.synchronize(context.Background(), Assignment{Pods: []Pod{p}}); err != nil {
		t.Fatal(err)
	}
	rt.channels[p.ID] <- processResult{code: 0}
	rt.running[p.ID] = false
	reports, err := a.reports()
	if err != nil {
		t.Fatal(err)
	}
	if reports[0].Phase != "Succeeded" || reports[0].ExitCode == nil || *reports[0].ExitCode != 0 || reports[0].Log != "runtime log" {
		t.Fatal("job result inaccurate")
	}
}
