package main

import (
	"context"
	"testing"
)

func TestMonitorExitDoesNotReleaseStillRunningContainer(t *testing.T) {
	dir := t.TempDir()
	rt := &fakeRuntime{running: map[string]bool{}, channels: map[string]chan processResult{}, dir: dir}
	a := &Agent{config: AgentConfig{ID: "worker-a", DataDir: dir}, runtime: rt, pods: map[string]*localPod{}, results: map[string]Operation{}}
	p := Pod{ID: "rcs-000000000001", NodeID: "worker-a"}
	if err := a.synchronize(context.Background(), Assignment{Pods: []Pod{p}}); err != nil {
		t.Fatal(err)
	}
	rt.channels[p.ID] <- processResult{code: 137}
	reports, err := a.reports()
	if err != nil {
		t.Fatal(err)
	}
	if reports[0].Phase != "Running" || reports[0].ExitCode != nil {
		t.Fatal("live container misreported as failed after monitor exit")
	}
}
