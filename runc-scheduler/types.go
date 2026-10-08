package main

import (
	"fmt"
	"regexp"
	"time"
)

type Resources struct {
	CPUMillis   int64 `json:"cpuMillis"`
	MemoryBytes int64 `json:"memoryBytes"`
}

func (r Resources) add(s Resources) Resources {
	return Resources{r.CPUMillis + s.CPUMillis, r.MemoryBytes + s.MemoryBytes}
}
func (r Resources) fits(s Resources) bool {
	return r.CPUMillis >= s.CPUMillis && r.MemoryBytes >= s.MemoryBytes
}

type Requirement struct {
	Key      string   `json:"key"`
	Operator string   `json:"operator"`
	Values   []string `json:"values,omitempty"`
}
type Selector map[string]string
type NodeTerm struct {
	MatchExpressions []Requirement `json:"matchExpressions"`
}
type PodAffinity struct {
	Selector    Selector `json:"selector"`
	TopologyKey string   `json:"topologyKey"`
}
type Taint struct {
	Key    string `json:"key"`
	Value  string `json:"value,omitempty"`
	Effect string `json:"effect"`
}
type Toleration struct {
	Key      string `json:"key"`
	Value    string `json:"value,omitempty"`
	Operator string `json:"operator,omitempty"`
	Effect   string `json:"effect,omitempty"`
}
type Template struct {
	Image           string            `json:"image"`
	Command         []string          `json:"command"`
	Env             map[string]string `json:"env,omitempty"`
	Labels          Selector          `json:"labels,omitempty"`
	Requests        Resources         `json:"requests"`
	Limits          Resources         `json:"limits"`
	NodeSelector    Selector          `json:"nodeSelector,omitempty"`
	NodeAffinity    []NodeTerm        `json:"nodeAffinity,omitempty"`
	PodAffinity     []PodAffinity     `json:"podAffinity,omitempty"`
	PodAntiAffinity []PodAffinity     `json:"podAntiAffinity,omitempty"`
	Tolerations     []Toleration      `json:"tolerations,omitempty"`
	HostPorts       []int             `json:"hostPorts,omitempty"`
	Priority        int               `json:"priority,omitempty"`
	Preemptible     *bool             `json:"preemptible,omitempty"`
}
type Autoscaler struct {
	Metric                 string  `json:"metric"`
	TargetPercent          float64 `json:"targetPercent"`
	MinReplicas            int     `json:"minReplicas"`
	MaxReplicas            int     `json:"maxReplicas"`
	ScaleDownWindowSeconds int     `json:"scaleDownWindowSeconds"`
	CooldownSeconds        int     `json:"cooldownSeconds"`
}
type Recommendation struct {
	At       time.Time `json:"at"`
	Replicas int       `json:"replicas"`
}
type Workload struct {
	Name            string           `json:"name"`
	Kind            string           `json:"kind"`
	Replicas        int              `json:"replicas"`
	Template        Template         `json:"template"`
	Autoscaler      *Autoscaler      `json:"autoscaler,omitempty"`
	Generation      int64            `json:"generation,omitempty"`
	Deleted         bool             `json:"deleted,omitempty"`
	LastScale       time.Time        `json:"lastScale,omitempty"`
	Recommendations []Recommendation `json:"recommendations,omitempty"`
}
type Node struct {
	ID            string    `json:"id"`
	Address       string    `json:"address"`
	Architecture  string    `json:"architecture"`
	CgroupMode    string    `json:"cgroupMode,omitempty"`
	Capacity      Resources `json:"capacity"`
	Images        []string  `json:"images"`
	Labels        Selector  `json:"labels"`
	Taints        []Taint   `json:"taints,omitempty"`
	Cordoned      bool      `json:"cordoned"`
	Ready         bool      `json:"ready"`
	LastHeartbeat time.Time `json:"lastHeartbeat"`
}
type Pod struct {
	ID               string    `json:"id"`
	Workload         string    `json:"workload"`
	Generation       int64     `json:"generation"`
	Spec             Template  `json:"spec"`
	NodeID           string    `json:"nodeId,omitempty"`
	NominatedNode    string    `json:"nominatedNode,omitempty"`
	Phase            string    `json:"phase"`
	Reason           string    `json:"reason,omitempty"`
	Created          time.Time `json:"created"`
	Updated          time.Time `json:"updated"`
	CPUUsageMillis   int64     `json:"cpuUsageMillis"`
	MemoryUsageBytes int64     `json:"memoryUsageBytes"`
	MetricsValid     bool      `json:"metricsValid"`
	ExitCode         *int      `json:"exitCode,omitempty"`
	Log              string    `json:"log,omitempty"`
}
type PodReport struct {
	ID               string `json:"id"`
	Phase            string `json:"phase"`
	Reason           string `json:"reason,omitempty"`
	CPUUsageMillis   int64  `json:"cpuUsageMillis"`
	MemoryUsageBytes int64  `json:"memoryUsageBytes"`
	MetricsValid     bool   `json:"metricsValid"`
	ExitCode         *int   `json:"exitCode,omitempty"`
	Log              string `json:"log,omitempty"`
}
type Operation struct {
	ID       string    `json:"id"`
	PodID    string    `json:"podId"`
	NodeID   string    `json:"nodeId"`
	Command  []string  `json:"command"`
	Status   string    `json:"status"`
	Output   string    `json:"output,omitempty"`
	ExitCode int       `json:"exitCode"`
	Created  time.Time `json:"created"`
}
type Heartbeat struct {
	Node    Node        `json:"node"`
	Pods    []PodReport `json:"pods"`
	Results []Operation `json:"results,omitempty"`
}
type Assignment struct {
	Pods       []Pod       `json:"pods"`
	Operations []Operation `json:"operations"`
}
type Event struct {
	At      time.Time `json:"at"`
	Subject string    `json:"subject"`
	Reason  string    `json:"reason"`
	Message string    `json:"message"`
}
type State struct {
	Version    int                   `json:"version"`
	Sequence   uint64                `json:"sequence"`
	Nodes      map[string]*Node      `json:"nodes"`
	Workloads  map[string]*Workload  `json:"workloads"`
	Pods       map[string]*Pod       `json:"pods"`
	Operations map[string]*Operation `json:"operations"`
	Events     []Event               `json:"events"`
}

func emptyState() State {
	return State{Version: 1, Nodes: map[string]*Node{}, Workloads: map[string]*Workload{}, Pods: map[string]*Pod{}, Operations: map[string]*Operation{}}
}
func terminal(p *Pod) bool {
	return p.Phase == "Succeeded" || p.Phase == "Failed" || p.Phase == "Removed"
}
func consumes(p *Pod) bool { return p.NodeID != "" && !terminal(p) }
func matches(labels, selector Selector) bool {
	for k, v := range selector {
		if x, ok := labels[k]; !ok || x != v {
			return false
		}
	}
	return true
}

var validName = regexp.MustCompile(`^[a-z][a-z0-9-]{0,62}$`)

func validateWorkload(w *Workload) error {
	if !validName.MatchString(w.Name) {
		return fmt.Errorf("name must match %s", validName)
	}
	if w.Kind == "" {
		w.Kind = "deployment"
	}
	if w.Kind != "deployment" && w.Kind != "job" {
		return fmt.Errorf("kind must be deployment or job")
	}
	if w.Replicas < 0 || w.Replicas > 10000 {
		return fmt.Errorf("replicas must be 0..10000")
	}
	t := &w.Template
	if !validName.MatchString(t.Image) || len(t.Command) == 0 || t.Command[0] == "" {
		return fmt.Errorf("image name and command are required")
	}
	if t.Requests.CPUMillis <= 0 || t.Requests.MemoryBytes <= 0 || t.Requests.CPUMillis > 100000000 || t.Requests.MemoryBytes > 1<<60 {
		return fmt.Errorf("positive bounded CPU/memory requests required")
	}
	if t.Limits.CPUMillis == 0 {
		t.Limits.CPUMillis = t.Requests.CPUMillis
	}
	if t.Limits.MemoryBytes == 0 {
		t.Limits.MemoryBytes = t.Requests.MemoryBytes
	}
	if !t.Limits.fits(t.Requests) || t.Limits.CPUMillis > 100000000 || t.Limits.MemoryBytes > 1<<60 {
		return fmt.Errorf("limits must be >= requests and within bounds")
	}
	ports := map[int]bool{}
	for _, p := range t.HostPorts {
		if p < 1 || p > 65535 || ports[p] {
			return fmt.Errorf("invalid or duplicate host port")
		}
		ports[p] = true
	}
	for _, a := range append(append([]PodAffinity{}, t.PodAffinity...), t.PodAntiAffinity...) {
		if len(a.Selector) == 0 || a.TopologyKey == "" {
			return fmt.Errorf("pod affinity requires nonempty selector and topologyKey")
		}
	}
	for _, term := range t.NodeAffinity {
		if len(term.MatchExpressions) == 0 {
			return fmt.Errorf("empty node affinity term")
		}
		for _, r := range term.MatchExpressions {
			switch r.Operator {
			case "In", "NotIn":
				if len(r.Values) == 0 {
					return fmt.Errorf("In/NotIn require values")
				}
			case "Exists", "DoesNotExist":
				if len(r.Values) != 0 {
					return fmt.Errorf("Exists/DoesNotExist cannot have values")
				}
			default:
				return fmt.Errorf("unsupported node affinity operator %s", r.Operator)
			}
			if r.Key == "" {
				return fmt.Errorf("empty affinity key")
			}
		}
	}
	for _, t := range t.Tolerations {
		if t.Operator != "" && t.Operator != "Equal" && t.Operator != "Exists" {
			return fmt.Errorf("invalid toleration operator")
		}
		if t.Key == "" && t.Operator != "Exists" {
			return fmt.Errorf("empty key requires Exists")
		}
		if t.Effect != "" && t.Effect != "NoSchedule" && t.Effect != "NoExecute" && t.Effect != "PreferNoSchedule" {
			return fmt.Errorf("invalid toleration effect")
		}
	}
	for k, v := range t.Env {
		if k == "" || regexp.MustCompile(`[=\x00]`).MatchString(k) || regexp.MustCompile(`\x00`).MatchString(v) {
			return fmt.Errorf("invalid environment")
		}
	}
	if w.Autoscaler != nil {
		a := w.Autoscaler
		if w.Kind != "deployment" || (a.Metric != "cpu" && a.Metric != "memory") || a.TargetPercent <= 0 || a.TargetPercent > 1000 || a.MinReplicas < 1 || a.MaxReplicas < a.MinReplicas || a.MaxReplicas > 10000 || a.ScaleDownWindowSeconds < 0 || a.ScaleDownWindowSeconds > 86400 || a.CooldownSeconds < 0 || a.CooldownSeconds > 86400 {
			return fmt.Errorf("invalid autoscaler")
		}
		if w.Replicas < a.MinReplicas || w.Replicas > a.MaxReplicas {
			return fmt.Errorf("replicas outside autoscaler bounds")
		}
	}
	return nil
}
