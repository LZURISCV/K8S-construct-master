package main

import (
	"fmt"
	"sort"
)

func nodeTermsMatch(n *Node, terms []NodeTerm) bool {
	if len(terms) == 0 {
		return true
	}
	for _, term := range terms {
		ok := true
		for _, r := range term.MatchExpressions {
			v, exists := n.Labels[r.Key]
			has := false
			for _, x := range r.Values {
				has = has || x == v
			}
			switch r.Operator {
			case "In":
				ok = ok && exists && has
			case "NotIn":
				ok = ok && (!exists || !has)
			case "Exists":
				ok = ok && exists
			case "DoesNotExist":
				ok = ok && !exists
			default:
				ok = false
			}
		}
		if ok {
			return true
		}
	}
	return false
}
func tolerates(ts []Toleration, t Taint) bool {
	for _, x := range ts {
		if x.Effect != "" && x.Effect != t.Effect {
			continue
		}
		if x.Operator == "Exists" {
			if x.Key == "" || x.Key == t.Key {
				return true
			}
		} else if x.Key == t.Key && x.Value == t.Value {
			return true
		}
	}
	return false
}
func usedOn(s *State, id string, exclude map[string]bool) Resources {
	u := Resources{}
	for _, p := range s.Pods {
		if p.NodeID == id && consumes(p) && !exclude[p.ID] {
			u = u.add(p.Spec.Requests)
		}
	}
	return u
}
func candidate(s *State, p *Pod, n *Node, exclude map[string]bool) (bool, string) {
	if !n.Ready || n.Cordoned {
		return false, "node not ready or cordoned"
	}
	image := false
	for _, i := range n.Images {
		image = image || i == p.Spec.Image
	}
	if !image {
		return false, "image unavailable"
	}
	if !matches(n.Labels, p.Spec.NodeSelector) || !nodeTermsMatch(n, p.Spec.NodeAffinity) {
		return false, "node affinity mismatch"
	}
	for _, t := range n.Taints {
		if (t.Effect == "NoSchedule" || t.Effect == "NoExecute") && !tolerates(p.Spec.Tolerations, t) {
			return false, "untolerated taint"
		}
	}
	if !n.Capacity.fits(usedOn(s, n.ID, exclude).add(p.Spec.Requests)) {
		return false, "insufficient resources"
	}
	for _, other := range s.Pods {
		if other.ID == p.ID || !consumes(other) || exclude[other.ID] {
			continue
		}
		if other.NodeID == n.ID {
			for _, a := range p.Spec.HostPorts {
				for _, b := range other.Spec.HostPorts {
					if a == b {
						return false, "host port occupied"
					}
				}
			}
		}
	}
	for _, a := range p.Spec.PodAffinity {
		domain, ok := n.Labels[a.TopologyKey]
		if !ok {
			return false, "missing affinity topology label"
		}
		found := false
		any := false
		for _, o := range s.Pods {
			if o.ID == p.ID || !consumes(o) || o.Phase == "Terminating" || exclude[o.ID] || !matches(o.Spec.Labels, a.Selector) {
				continue
			}
			any = true
			on := s.Nodes[o.NodeID]
			if on != nil {
				d, exists := on.Labels[a.TopologyKey]
				found = found || (exists && d == domain)
			}
		}
		if !found && !(matches(p.Spec.Labels, a.Selector) && !any) {
			return false, "pod affinity mismatch"
		}
	}
	for _, a := range p.Spec.PodAntiAffinity {
		domain, ok := n.Labels[a.TopologyKey]
		if !ok {
			return false, "missing anti-affinity topology label"
		}
		for _, o := range s.Pods {
			if o.ID == p.ID || !consumes(o) || exclude[o.ID] || !matches(o.Spec.Labels, a.Selector) {
				continue
			}
			on := s.Nodes[o.NodeID]
			if on != nil {
				d, exists := on.Labels[a.TopologyKey]
				if exists && d == domain {
					return false, "pod anti-affinity mismatch"
				}
			}
		}
	}
	// Existing pods' required anti-affinity is also a hard constraint for newcomers.
	for _, o := range s.Pods {
		if !consumes(o) || exclude[o.ID] {
			continue
		}
		on := s.Nodes[o.NodeID]
		if on == nil {
			continue
		}
		for _, a := range o.Spec.PodAntiAffinity {
			d, ok := on.Labels[a.TopologyKey]
			nd, nok := n.Labels[a.TopologyKey]
			if ok && nok && d == nd && matches(p.Spec.Labels, a.Selector) {
				return false, "existing pod anti-affinity"
			}
		}
	}
	for _, o := range s.Pods {
		if o.Phase == "Pending" && o.NominatedNode == n.ID && o.Spec.Priority > p.Spec.Priority {
			return false, "reserved for higher priority pod"
		}
	}
	return true, ""
}
func nodeScore(s *State, p *Pod, n *Node, exclude map[string]bool) float64 {
	u := usedOn(s, n.ID, exclude).add(p.Spec.Requests)
	score := 100 - (float64(u.CPUMillis)/float64(n.Capacity.CPUMillis)+float64(u.MemoryBytes)/float64(n.Capacity.MemoryBytes))*50
	for _, t := range n.Taints {
		if t.Effect == "PreferNoSchedule" && !tolerates(p.Spec.Tolerations, t) {
			score -= 100
		}
	}
	return score
}
func schedule(s *State, p *Pod) (string, []string, string) {
	nodes := make([]*Node, 0, len(s.Nodes))
	for _, n := range s.Nodes {
		nodes = append(nodes, n)
	}
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].ID < nodes[j].ID })
	best := ""
	score := -1e9
	reasons := map[string]int{}
	for _, n := range nodes {
		ok, r := candidate(s, p, n, nil)
		if !ok {
			reasons[r]++
			continue
		}
		v := nodeScore(s, p, n, nil)
		if v > score {
			best = n.ID
			score = v
		}
	}
	if best != "" {
		return best, nil, ""
	}
	var chosen []string
	bestVictimPriority := int(^uint(0) >> 1)
	for _, n := range nodes {
		var victims []*Pod
		for _, o := range s.Pods {
			if o.NodeID == n.ID && consumes(o) && o.Phase != "Terminating" && o.Spec.Priority < p.Spec.Priority && (o.Spec.Preemptible == nil || *o.Spec.Preemptible) {
				victims = append(victims, o)
			}
		}
		sort.Slice(victims, func(i, j int) bool {
			if victims[i].Spec.Priority != victims[j].Spec.Priority {
				return victims[i].Spec.Priority < victims[j].Spec.Priority
			}
			return victims[i].ID < victims[j].ID
		})
		excluded := map[string]bool{}
		var ids []string
		for _, v := range victims {
			excluded[v.ID] = true
			ids = append(ids, v.ID)
			ok, _ := candidate(s, p, n, excluded)
			if ok {
				vp := v.Spec.Priority
				if chosen == nil || vp < bestVictimPriority || (vp == bestVictimPriority && len(ids) < len(chosen)) {
					chosen = append([]string{}, ids...)
					best = n.ID
					bestVictimPriority = vp
				}
				break
			}
		}
	}
	if chosen != nil {
		return best, chosen, "waiting for preempted containers to stop"
	}
	keys := make([]string, 0, len(reasons))
	for r := range reasons {
		keys = append(keys, r)
	}
	sort.Strings(keys)
	msg := "no eligible nodes"
	for _, r := range keys {
		msg += fmt.Sprintf("; %s=%d", r, reasons[r])
	}
	return "", nil, msg
}
