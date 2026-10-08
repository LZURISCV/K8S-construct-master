package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"time"
)

type AgentConfig struct {
	ID               string            `json:"id"`
	Address          string            `json:"address"`
	ControlURL       string            `json:"controlURL"`
	NodeTokenFile    string            `json:"nodeTokenFile"`
	CAFile           string            `json:"caFile,omitempty"`
	DataDir          string            `json:"dataDir"`
	Runc             string            `json:"runc"`
	RuncRoot         string            `json:"runcRoot"`
	Images           map[string]string `json:"images"`
	Labels           Selector          `json:"labels,omitempty"`
	Capacity         Resources         `json:"capacity,omitempty"`
	Reserve          Resources         `json:"reserve"`
	HeartbeatSeconds int               `json:"heartbeatSeconds"`
	SystemdCgroup    bool              `json:"systemdCgroup"`
	CgroupMode       string            `json:"cgroupMode,omitempty"`
}
type localPod struct {
	Pod      Pod       `json:"pod"`
	Phase    string    `json:"phase"`
	Reason   string    `json:"reason,omitempty"`
	ExitCode *int      `json:"exitCode,omitempty"`
	Finished time.Time `json:"finished,omitempty"`
	metrics  Metrics
	done     <-chan processResult
}
type Metrics struct {
	CPUUsageMillis   int64
	MemoryUsageBytes int64
	Valid            bool
	total            uint64
	sample           time.Time
}
type processResult struct {
	code int
	err  error
}

var errRuntimeUncertain = errors.New("container may still be running; agent must recover before reporting failure")

type Runtime interface {
	Start(Pod) (<-chan processResult, error)
	Inspect(string) (string, error)
	Stop(string) error
	Stats(string, Metrics) Metrics
	Exec(context.Context, string, []string) (string, int)
	Logs(string) string
	Remove(string) error
}
type Agent struct {
	config  AgentConfig
	client  *Client
	runtime Runtime
	pods    map[string]*localPod
	results map[string]Operation
	node    Node
}

func localFile(c AgentConfig, id string) string {
	return filepath.Join(c.DataDir, "pods", id, "local.json")
}
func newAgent(c AgentConfig) (*Agent, error) {
	if !validName.MatchString(c.ID) || c.DataDir == "" || c.RuncRoot == "" || len(c.Images) == 0 {
		return nil, fmt.Errorf("agent id, dataDir, runcRoot and images required")
	}
	for name, path := range c.Images {
		if !validName.MatchString(name) || !filepath.IsAbs(path) {
			return nil, fmt.Errorf("image requires valid name and absolute rootfs path")
		}
		st, err := os.Stat(path)
		if err != nil || !st.IsDir() {
			return nil, fmt.Errorf("image rootfs unavailable: %s", path)
		}
	}
	if c.HeartbeatSeconds < 1 {
		c.HeartbeatSeconds = 3
	}
	if c.Runc == "" {
		c.Runc = "runc"
	}
	token, err := readToken(c.NodeTokenFile)
	if err != nil {
		return nil, err
	}
	client, err := newClient(c.ControlURL, token, c.CAFile)
	if err != nil {
		return nil, err
	}
	capacity, arch, err := machineCapacity()
	if err != nil {
		return nil, err
	}
	if c.Capacity.CPUMillis > 0 {
		if c.Capacity.CPUMillis > capacity.CPUMillis {
			return nil, fmt.Errorf("configured CPU exceeds physical capacity")
		}
		capacity.CPUMillis = c.Capacity.CPUMillis
	}
	if c.Capacity.MemoryBytes > 0 {
		if c.Capacity.MemoryBytes > capacity.MemoryBytes {
			return nil, fmt.Errorf("configured memory exceeds physical capacity")
		}
		capacity.MemoryBytes = c.Capacity.MemoryBytes
	}
	capacity.CPUMillis -= c.Reserve.CPUMillis
	capacity.MemoryBytes -= c.Reserve.MemoryBytes
	if c.Reserve.CPUMillis < 0 || c.Reserve.MemoryBytes < 0 || !capacity.fits(Resources{1, 1}) {
		return nil, fmt.Errorf("invalid reserved resources")
	}
	rt, err := newRuncRuntime(c)
	if err != nil {
		return nil, err
	}
	a := &Agent{config: c, client: client, runtime: rt, pods: map[string]*localPod{}, results: map[string]Operation{}, node: Node{ID: c.ID, Address: c.Address, Architecture: arch, Capacity: capacity, Labels: c.Labels}}
	if info, ok := rt.(interface{ CgroupVersion() string }); ok {
		a.node.CgroupMode = info.CgroupVersion()
	}
	for name := range c.Images {
		a.node.Images = append(a.node.Images, name)
	}
	sort.Strings(a.node.Images)
	files, err := filepath.Glob(filepath.Join(c.DataDir, "pods", "*", "local.json"))
	if err != nil {
		return nil, err
	}
	for _, file := range files {
		var p localPod
		if err := readJSON(file, &p); err != nil {
			return nil, fmt.Errorf("local state %s: %w", file, err)
		}
		if !validPodID(p.Pod.ID) {
			return nil, fmt.Errorf("invalid local pod ID")
		}
		if p.Phase == "Running" || p.Phase == "Assigned" {
			state, err := rt.Inspect(p.Pod.ID)
			if err != nil {
				return nil, fmt.Errorf("cannot recover runtime state for %s: %w", p.Pod.ID, err)
			}
			if state == "running" || state == "paused" {
				p.Phase = "Running"
			} else {
				code := -1
				p.Phase = "Failed"
				p.ExitCode = &code
				p.Reason = "agent restarted; container no longer running, exit code unknown"
				p.Finished = time.Now().UTC()
			}
		}
		a.pods[p.Pod.ID] = &p
	}
	return a, nil
}
func validPodID(id string) bool {
	if len(id) != 16 || id[:4] != "rcs-" {
		return false
	}
	for _, c := range id[4:] {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}
func (a *Agent) save(p *localPod) error { return atomicJSON(localFile(a.config, p.Pod.ID), p) }
func (a *Agent) reports() ([]PodReport, error) {
	var reports []PodReport
	for _, p := range a.pods {
		if p.done != nil {
			select {
			case r := <-p.done:
				p.done = nil
				state, inspectErr := a.runtime.Inspect(p.Pod.ID)
				if inspectErr != nil {
					return nil, inspectErr
				}
				if state == "running" || state == "paused" {
					p.Phase = "Running"
					p.ExitCode = nil
					p.Reason = "runtime monitor exited; live container recovered"
					if err := a.save(p); err != nil {
						return nil, err
					}
					break
				}
				p.ExitCode = &r.code
				p.Finished = time.Now().UTC()
				if r.code == 0 && r.err == nil {
					p.Phase = "Succeeded"
					p.Reason = ""
				} else {
					p.Phase = "Failed"
					if r.err != nil {
						p.Reason = r.err.Error()
					}
				}
				if err := a.save(p); err != nil {
					return nil, err
				}
			default:
			}
		}
		if p.Phase == "Running" && p.done == nil {
			state, err := a.runtime.Inspect(p.Pod.ID)
			if err != nil {
				return nil, err
			}
			if state != "running" && state != "paused" {
				code := -1
				p.ExitCode = &code
				p.Phase = "Failed"
				p.Finished = time.Now().UTC()
				p.Reason = "recovered container stopped; exit status unavailable"
				if err := a.save(p); err != nil {
					return nil, err
				}
			}
		}
		if p.Phase == "Running" {
			p.metrics = a.runtime.Stats(p.Pod.ID, p.metrics)
		} else {
			p.metrics.Valid = false
		}
		reports = append(reports, PodReport{ID: p.Pod.ID, Phase: p.Phase, Reason: p.Reason, CPUUsageMillis: p.metrics.CPUUsageMillis, MemoryUsageBytes: p.metrics.MemoryUsageBytes, MetricsValid: p.metrics.Valid, ExitCode: p.ExitCode, Log: a.runtime.Logs(p.Pod.ID)})
	}
	sort.Slice(reports, func(i, j int) bool { return reports[i].ID < reports[j].ID })
	return reports, nil
}
func (a *Agent) synchronize(ctx context.Context, want Assignment) error {
	desired := map[string]Pod{}
	for _, p := range want.Pods {
		if !validPodID(p.ID) || p.NodeID != a.config.ID {
			return fmt.Errorf("invalid controller assignment")
		}
		desired[p.ID] = p
	}
	// Stop first; resources/ports may be reused by the following assignment only after an acknowledgement.
	for id, p := range a.pods {
		if _, ok := desired[id]; ok {
			continue
		}
		if err := a.runtime.Stop(id); err != nil {
			return fmt.Errorf("stop %s: %w", id, err)
		}
		if p.done != nil {
			select {
			case <-p.done:
			case <-time.After(5 * time.Second):
				return fmt.Errorf("runtime monitor did not exit for %s", id)
			}
		}
		if err := a.runtime.Remove(id); err != nil {
			return err
		}
		delete(a.pods, id)
	}
	for _, p := range want.Pods {
		if _, ok := a.pods[p.ID]; ok {
			continue
		}
		local := &localPod{Pod: p, Phase: "Assigned"}
		a.pods[p.ID] = local
		if err := a.save(local); err != nil {
			return err
		}
		ch, err := a.runtime.Start(p)
		if err != nil {
			if errors.Is(err, errRuntimeUncertain) {
				return err
			}
			code := -1
			local.Phase = "Failed"
			local.ExitCode = &code
			local.Reason = err.Error()
			local.Finished = time.Now().UTC()
		} else {
			local.Phase = "Running"
			local.done = ch
		}
		if err := a.save(local); err != nil {
			return err
		}
	}
	for _, op := range want.Operations {
		if _, ok := a.results[op.ID]; ok {
			continue
		}
		p := a.pods[op.PodID]
		if p == nil || p.Phase != "Running" {
			op.ExitCode = -1
			op.Output = "pod is not running"
		} else {
			opctx, cancel := context.WithTimeout(ctx, 20*time.Second)
			op.Output, op.ExitCode = a.runtime.Exec(opctx, op.PodID, op.Command)
			cancel()
		}
		op.Status = "Done"
		a.results[op.ID] = op
	}
	// A delivered exec is not replayed while acknowledgement is pending.
	wantedOps := map[string]bool{}
	for _, op := range want.Operations {
		wantedOps[op.ID] = true
	}
	for id := range a.results {
		if !wantedOps[id] {
			delete(a.results, id)
		}
	}
	return nil
}
func (a *Agent) run(ctx context.Context) error {
	interval := time.Duration(a.config.HeartbeatSeconds) * time.Second
	for {
		pods, err := a.reports()
		if err != nil {
			return err
		}
		h := Heartbeat{Node: a.node, Pods: pods}
		for _, op := range a.results {
			h.Results = append(h.Results, op)
		}
		var want Assignment
		err = a.client.request(ctx, "POST", "/v1/heartbeat", h, &want)
		if err != nil {
			log.Printf("heartbeat failed; retaining current containers: %v", err)
		} else if err = a.synchronize(ctx, want); err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(interval):
		}
	}
}
