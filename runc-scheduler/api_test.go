package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestControllerAPIMultiNodeRoundTrip(t *testing.T) {
	e := testEngine(t)
	admin := strings.Repeat("a", 64)
	nodes := strings.Repeat("b", 64)
	server := httptest.NewServer(apiHandler(e, admin, nodes))
	defer server.Close()
	ctx := context.Background()
	cli, _ := newClient(server.URL, admin, "")
	agent, _ := newClient(server.URL, nodes, "")
	for _, id := range []string{"worker-a", "worker-b", "worker-c"} {
		var a Assignment
		if err := agent.request(ctx, "POST", "/v1/heartbeat", Heartbeat{Node: testNode(id, 1000)}, &a); err != nil {
			t.Fatal(err)
		}
	}
	w := testWorkload("multi", 3, 800)
	if err := cli.request(ctx, "POST", "/v1/workloads", w, nil); err != nil {
		t.Fatal(err)
	}
	var s State
	if err := cli.request(ctx, "GET", "/v1/state", nil, &s); err != nil {
		t.Fatal(err)
	}
	placed := map[string]bool{}
	for _, p := range s.Pods {
		placed[p.NodeID] = true
	}
	if len(placed) != 3 || placed[""] {
		t.Fatal("failed to place on three independent agents")
	}
	if err := agent.request(ctx, "GET", "/v1/state", nil, &s); err == nil {
		t.Fatal("node credentials accessed admin API")
	}
	if err := cli.request(ctx, "POST", "/v1/heartbeat", Heartbeat{Node: testNode("worker-a", 1000)}, nil); err == nil {
		t.Fatal("admin credentials used as node credentials")
	}
	if err := cli.request(ctx, "DELETE", "/v1/workloads/multi", nil, nil); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"worker-a", "worker-b", "worker-c"} {
		var a Assignment
		if err := agent.request(ctx, "POST", "/v1/heartbeat", Heartbeat{Node: testNode(id, 1000)}, &a); err != nil {
			t.Fatal(err)
		}
		if len(a.Pods) != 0 {
			t.Fatal("deleted workload still assigned")
		}
	}
}
func TestUnauthorizedAndMalformedAPI(t *testing.T) {
	e := testEngine(t)
	server := httptest.NewServer(apiHandler(e, "admin", "node"))
	defer server.Close()
	r, err := http.Get(server.URL + "/v1/state")
	if err != nil {
		t.Fatal(err)
	}
	r.Body.Close()
	if r.StatusCode != 401 {
		t.Fatal(r.StatusCode)
	}
	req, _ := http.NewRequest("POST", server.URL+"/v1/workloads", strings.NewReader(`{"name":"x","typo":1}`))
	req.Header.Set("Authorization", "Bearer admin")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 400 {
		t.Fatal("unknown fields silently accepted")
	}
}
func TestExecResultNodeOwnership(t *testing.T) {
	e := testEngine(t)
	addNode(t, e, "worker-a", 1000)
	addNode(t, e, "worker-b", 1000)
	applyTest(t, e, testWorkload("exec", 1, 100))
	p := podOf(t, e.snapshot(), "exec", "")
	reportRunning(t, e, p.NodeID, testNow, time.Now().Unix())
	id, err := e.exec(p.ID, []string{"/bin/true"}, testNow)
	if err != nil {
		t.Fatal(err)
	}
	other := "worker-b"
	if p.NodeID == other {
		other = "worker-a"
	}
	_, err = e.heartbeat(Heartbeat{Node: testNode(other, 1000), Results: []Operation{{ID: id, Output: "forged", Status: "Done"}}}, testNow)
	if err != nil {
		t.Fatal(err)
	}
	if e.snapshot().Operations[id].Status != "Pending" {
		t.Fatal("wrong node forged exec result")
	}
	_, err = e.heartbeat(Heartbeat{Node: testNode(p.NodeID, 1000), Pods: []PodReport{{ID: p.ID, Phase: "Running"}}, Results: []Operation{{ID: id, Output: "done", Status: "Done"}}}, testNow)
	if err != nil {
		t.Fatal(err)
	}
	if e.snapshot().Operations[id].Output != "done" {
		t.Fatal("exec result missing")
	}
}
