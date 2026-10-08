package main

import (
	"encoding/json"
	"fmt"
	"math"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"sync"
	"time"
)

type Engine struct {
	mu      sync.Mutex
	state   State
	path    string
	timeout time.Duration
}

func newEngine(path string, timeout time.Duration) (*Engine, error) {
	e := &Engine{state: emptyState(), path: path, timeout: timeout}
	if path != "" {
		b, err := os.ReadFile(path)
		if err == nil {
			if err = json.Unmarshal(b, &e.state); err != nil {
				return nil, err
			}
			if e.state.Version != 1 || e.state.Nodes == nil || e.state.Workloads == nil || e.state.Pods == nil || e.state.Operations == nil {
				return nil, fmt.Errorf("invalid state file")
			}
		} else if !os.IsNotExist(err) {
			return nil, err
		}
	}
	return e, nil
}
func atomicJSON(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".state-*")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if err = f.Chmod(0600); err == nil {
		_, err = f.Write(b)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err = os.Rename(tmp, path); err != nil {
		return err
	}
	return syncDirectory(filepath.Dir(path))
}
func (e *Engine) transaction(fn func(*State) error) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	old, err := json.Marshal(e.state)
	if err != nil {
		return err
	}
	if err = fn(&e.state); err == nil && e.path != "" {
		err = atomicJSON(e.path, &e.state)
	}
	if err != nil {
		e.state = State{}
		_ = json.Unmarshal(old, &e.state)
	}
	return err
}
func (e *Engine) snapshot() State {
	e.mu.Lock()
	defer e.mu.Unlock()
	b, _ := json.Marshal(e.state)
	var s State
	_ = json.Unmarshal(b, &s)
	return s
}
func event(s *State, now time.Time, subject, reason, msg string) {
	s.Events = append(s.Events, Event{now, subject, reason, msg})
	if len(s.Events) > 1000 {
		s.Events = s.Events[len(s.Events)-1000:]
	}
}
func nextID(s *State, prefix string) string {
	s.Sequence++
	return fmt.Sprintf("%s-%012x", prefix, s.Sequence)
}
func stopPod(s *State, p *Pod, now time.Time, reason string) {
	if terminal(p) || p.Phase == "Terminating" {
		return
	}
	if p.NodeID == "" {
		p.Phase = "Removed"
	} else {
		p.Phase = "Terminating"
	}
	p.Reason = reason
	p.Updated = now
	p.MetricsValid = false
	event(s, now, p.ID, "Stopping", reason)
}
func (e *Engine) apply(w Workload, now time.Time) error {
	if err := validateWorkload(&w); err != nil {
		return err
	}
	return e.transaction(func(s *State) error {
		old := s.Workloads[w.Name]
		w.Generation = 1
		w.Deleted = false
		w.Recommendations = nil
		w.LastScale = now
		if old != nil {
			w.Generation = old.Generation
			if old.Deleted || !reflect.DeepEqual(old.Template, w.Template) || old.Kind != w.Kind {
				w.Generation++
			}
		}
		s.Workloads[w.Name] = &w
		event(s, now, w.Name, "Applied", fmt.Sprintf("generation=%d replicas=%d", w.Generation, w.Replicas))
		reconcile(s, now, e.timeout)
		return nil
	})
}
func (e *Engine) remove(name string, now time.Time) error {
	return e.transaction(func(s *State) error {
		w := s.Workloads[name]
		if w == nil {
			return fmt.Errorf("workload not found")
		}
		w.Deleted = true
		w.Replicas = 0
		reconcile(s, now, e.timeout)
		event(s, now, name, "Deleted", "workload removed")
		return nil
	})
}
func (e *Engine) scale(name string, replicas int, now time.Time) error {
	return e.transaction(func(s *State) error {
		w := s.Workloads[name]
		if w == nil || w.Deleted {
			return fmt.Errorf("workload not found")
		}
		if replicas < 0 || replicas > 10000 {
			return fmt.Errorf("invalid replica count")
		}
		if w.Autoscaler != nil {
			return fmt.Errorf("remove autoscaler before manually scaling")
		}
		w.Replicas = replicas
		w.LastScale = now
		reconcile(s, now, e.timeout)
		return nil
	})
}

type NodePatch struct {
	Labels   map[string]*string `json:"labels,omitempty"`
	Taints   *[]Taint           `json:"taints,omitempty"`
	Cordoned *bool              `json:"cordoned,omitempty"`
}

func (e *Engine) patchNode(id string, p NodePatch, now time.Time) error {
	for k := range p.Labels {
		if k == "rcs.node" || k == "rcs.arch" {
			return fmt.Errorf("reserved label %s", k)
		}
	}
	if p.Taints != nil {
		for _, t := range *p.Taints {
			if t.Key == "" || (t.Effect != "NoSchedule" && t.Effect != "NoExecute" && t.Effect != "PreferNoSchedule") {
				return fmt.Errorf("invalid taint")
			}
		}
	}
	return e.transaction(func(s *State) error {
		n := s.Nodes[id]
		if n == nil {
			return fmt.Errorf("node not found")
		}
		for k, v := range p.Labels {
			if v == nil {
				delete(n.Labels, k)
			} else {
				n.Labels[k] = *v
			}
		}
		if p.Taints != nil {
			n.Taints = *p.Taints
		}
		if p.Cordoned != nil {
			n.Cordoned = *p.Cordoned
		}
		reconcile(s, now, e.timeout)
		event(s, now, id, "Configured", "node labels/taints/cordon updated")
		return nil
	})
}
func (e *Engine) heartbeat(h Heartbeat, now time.Time) (Assignment, error) {
	var result Assignment
	if !validName.MatchString(h.Node.ID) || !h.Node.Capacity.fits(Resources{1, 1}) || h.Node.Capacity.CPUMillis > 100000000 || h.Node.Capacity.MemoryBytes > 1<<60 || net.ParseIP(h.Node.Address) == nil {
		return result, fmt.Errorf("invalid node identity, address or capacity")
	}
	for _, image := range h.Node.Images {
		if !validName.MatchString(image) {
			return result, fmt.Errorf("invalid image name")
		}
	}
	seen := map[string]bool{}
	for _, r := range h.Pods {
		if seen[r.ID] {
			return result, fmt.Errorf("duplicate pod report")
		}
		seen[r.ID] = true
		if r.Phase != "Running" && r.Phase != "Assigned" && r.Phase != "Succeeded" && r.Phase != "Failed" {
			return result, fmt.Errorf("invalid reported phase")
		}
		if r.Phase == "Succeeded" && (r.ExitCode == nil || *r.ExitCode != 0) {
			return result, fmt.Errorf("success requires exit code zero")
		}
	}
	err := e.transaction(func(s *State) error {
		n := s.Nodes[h.Node.ID]
		if n == nil {
			copy := h.Node
			copy.Taints = nil
			copy.Cordoned = false
			copy.Labels = Selector{}
			for k, v := range h.Node.Labels {
				copy.Labels[k] = v
			}
			n = &copy
			s.Nodes[n.ID] = n
			event(s, now, n.ID, "Registered", "deployment node registered")
		}
		n.Capacity = h.Node.Capacity
		n.Address = h.Node.Address
		n.Architecture = h.Node.Architecture
		n.CgroupMode = h.Node.CgroupMode
		n.Images = h.Node.Images
		n.LastHeartbeat = now
		if !n.Ready {
			event(s, now, n.ID, "Ready", "heartbeat received")
		}
		n.Ready = true
		n.Labels["rcs.node"] = n.ID
		n.Labels["rcs.arch"] = n.Architecture
		for _, r := range h.Pods {
			p := s.Pods[r.ID]
			if p == nil || p.NodeID != n.ID || terminal(p) {
				continue
			}
			p.Log = r.Log
			if len(p.Log) > 32768 {
				p.Log = p.Log[len(p.Log)-32768:]
			}
			p.CPUUsageMillis = r.CPUUsageMillis
			p.MemoryUsageBytes = r.MemoryUsageBytes
			p.MetricsValid = r.MetricsValid && r.CPUUsageMillis >= 0 && r.MemoryUsageBytes >= 0
			p.ExitCode = r.ExitCode
			if p.Phase == "Terminating" {
				if r.Phase == "Succeeded" || r.Phase == "Failed" {
					p.Phase = "Removed"
					p.Updated = now
					event(s, now, p.ID, "Removed", "runtime confirmed container stopped")
				}
			} else {
				if p.Phase != r.Phase {
					event(s, now, p.ID, r.Phase, r.Reason)
				}
				p.Phase = r.Phase
				p.Reason = r.Reason
				p.Updated = now
			}
		}
		// Terminating reservations are released only after a full inventory acknowledges absence.
		for _, p := range s.Pods {
			if p.NodeID == n.ID && p.Phase == "Terminating" && !seen[p.ID] {
				p.Phase = "Removed"
				p.Updated = now
				event(s, now, p.ID, "Removed", "agent acknowledged absence")
			}
		}
		for _, r := range h.Results {
			op := s.Operations[r.ID]
			if op != nil && op.NodeID == n.ID && op.Status == "Pending" {
				op.Status = "Done"
				op.Output = r.Output
				if len(op.Output) > 65536 {
					op.Output = op.Output[:65536]
				}
				op.ExitCode = r.ExitCode
			}
		}
		reconcile(s, now, e.timeout)
		result.Pods = []Pod{}
		result.Operations = []Operation{}
		for _, p := range s.Pods {
			if p.NodeID == n.ID && !terminal(p) && p.Phase != "Terminating" {
				result.Pods = append(result.Pods, *p)
			}
		}
		for _, op := range s.Operations {
			if op.NodeID == n.ID && op.Status == "Pending" {
				result.Operations = append(result.Operations, *op)
			}
		}
		sort.Slice(result.Pods, func(i, j int) bool { return result.Pods[i].ID < result.Pods[j].ID })
		sort.Slice(result.Operations, func(i, j int) bool { return result.Operations[i].ID < result.Operations[j].ID })
		return nil
	})
	return result, err
}
func (e *Engine) exec(pod string, command []string, now time.Time) (string, error) {
	if len(command) == 0 || command[0] == "" {
		return "", fmt.Errorf("command required")
	}
	var id string
	err := e.transaction(func(s *State) error {
		p := s.Pods[pod]
		if p == nil || p.Phase != "Running" || s.Nodes[p.NodeID] == nil || !s.Nodes[p.NodeID].Ready {
			return fmt.Errorf("pod is not running on a ready node")
		}
		id = nextID(s, "op")
		s.Operations[id] = &Operation{ID: id, PodID: pod, NodeID: p.NodeID, Command: command, Status: "Pending", Created: now}
		return nil
	})
	return id, err
}
func (e *Engine) tick(now time.Time) error {
	return e.transaction(func(s *State) error { reconcile(s, now, e.timeout); return nil })
}
func autoscale(s *State, w *Workload, now time.Time) {
	a := w.Autoscaler
	if a == nil || w.Deleted || w.Replicas == 0 {
		return
	}
	count := 0
	sum := 0.0
	for _, p := range s.Pods {
		if p.Workload != w.Name || p.Generation != w.Generation || p.Phase != "Running" || !p.MetricsValid || s.Nodes[p.NodeID] == nil || !s.Nodes[p.NodeID].Ready {
			continue
		}
		if a.Metric == "cpu" {
			sum += float64(p.CPUUsageMillis) / float64(p.Spec.Requests.CPUMillis) * 100
		} else {
			sum += float64(p.MemoryUsageBytes) / float64(p.Spec.Requests.MemoryBytes) * 100
		}
		count++
	}
	if count == 0 {
		return
	}
	ratio := sum / float64(count) / a.TargetPercent
	if math.Abs(ratio-1) <= 0.1 {
		ratio = 1
	}
	desired := int(math.Ceil(float64(w.Replicas) * ratio))
	if desired < a.MinReplicas {
		desired = a.MinReplicas
	}
	if desired > a.MaxReplicas {
		desired = a.MaxReplicas
	}
	// Missing/pending replica metrics must never trigger scale down.
	if desired < w.Replicas && count < w.Replicas {
		desired = w.Replicas
	}
	window := time.Duration(a.ScaleDownWindowSeconds) * time.Second
	kept := []Recommendation{}
	for _, r := range w.Recommendations {
		if now.Sub(r.At) <= window {
			kept = append(kept, r)
		}
	}
	kept = append(kept, Recommendation{now, desired})
	w.Recommendations = kept
	if desired < w.Replicas {
		for _, r := range kept {
			if r.Replicas > desired {
				desired = r.Replicas
			}
		}
	}
	if desired != w.Replicas && now.Sub(w.LastScale) >= time.Duration(a.CooldownSeconds)*time.Second {
		old := w.Replicas
		w.Replicas = desired
		w.LastScale = now
		event(s, now, w.Name, "Autoscaled", fmt.Sprintf("%d -> %d using %s utilization %.1f%%", old, desired, a.Metric, sum/float64(count)))
	}
}
func reconcile(s *State, now time.Time, timeout time.Duration) {
	for _, n := range s.Nodes {
		if n.Ready && now.Sub(n.LastHeartbeat) > timeout {
			n.Ready = false
			event(s, now, n.ID, "NotReady", "heartbeat expired; existing reservations retained")
		}
	}
	for _, p := range s.Pods {
		if consumes(p) {
			n := s.Nodes[p.NodeID]
			if n == nil || !n.Ready {
				p.MetricsValid = false
				continue
			}
			for _, t := range n.Taints {
				if t.Effect == "NoExecute" && !tolerates(p.Spec.Tolerations, t) {
					stopPod(s, p, now, "untolerated NoExecute taint")
				}
			}
		}
	}
	names := make([]string, 0, len(s.Workloads))
	for name := range s.Workloads {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		w := s.Workloads[name]
		autoscale(s, w, now)
		var active []*Pod
		done := 0
		recentFailure := false
		for _, p := range s.Pods {
			if p.Workload != name {
				continue
			}
			if (w.Deleted || p.Generation != w.Generation) && !terminal(p) {
				stopPod(s, p, now, "workload deleted or replaced")
				continue
			}
			if p.Generation != w.Generation {
				continue
			}
			if p.Phase == "Failed" && now.Sub(p.Updated) < 10*time.Second {
				recentFailure = true
			}
			if !terminal(p) && p.Phase != "Terminating" {
				active = append(active, p)
			}
			if w.Kind == "job" && (p.Phase == "Succeeded" || p.Phase == "Failed") {
				done++
			}
		}
		wanted := w.Replicas - done
		if w.Deleted || wanted < 0 {
			wanted = 0
		}
		sort.Slice(active, func(i, j int) bool {
			if active[i].Phase == "Pending" && active[j].Phase != "Pending" {
				return true
			}
			if active[j].Phase == "Pending" && active[i].Phase != "Pending" {
				return false
			}
			return active[i].Created.After(active[j].Created)
		})
		for len(active) > wanted {
			stopPod(s, active[0], now, "replica count reduced")
			active = active[1:]
		}
		if w.Kind == "deployment" && recentFailure {
			continue
		}
		for len(active) < wanted {
			p := &Pod{ID: nextID(s, "rcs"), Workload: name, Generation: w.Generation, Spec: w.Template, Phase: "Pending", Created: now, Updated: now}
			s.Pods[p.ID] = p
			active = append(active, p)
			event(s, now, p.ID, "Created", name)
		}
	}
	var pending []*Pod
	for _, p := range s.Pods {
		if p.Phase == "Pending" {
			pending = append(pending, p)
		}
	}
	sort.Slice(pending, func(i, j int) bool {
		if pending[i].Spec.Priority != pending[j].Spec.Priority {
			return pending[i].Spec.Priority > pending[j].Spec.Priority
		}
		return pending[i].ID < pending[j].ID
	})
	for _, p := range pending {
		node, victims, reason := schedule(s, p)
		p.Reason = reason
		if len(victims) > 0 {
			p.NominatedNode = node
			for _, id := range victims {
				stopPod(s, s.Pods[id], now, "preempted by "+p.ID)
			}
			continue
		}
		if node != "" {
			p.NodeID = node
			p.NominatedNode = ""
			p.Phase = "Assigned"
			p.Updated = now
			event(s, now, p.ID, "Scheduled", node)
		}
	}
	for _, op := range s.Operations {
		if op.Status == "Pending" && now.Sub(op.Created) > 90*time.Second {
			op.Status = "Done"
			op.ExitCode = -1
			op.Output = "exec operation timed out; command delivery/result is uncertain"
		}
	}
}
