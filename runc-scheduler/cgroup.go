package main

import (
	"fmt"
	"math"
	"path"
	"strconv"
	"strings"
	"time"
)

type CgroupMount struct {
	Root       string `json:"root"`
	Mountpoint string `json:"mountpoint"`
	Writable   bool   `json:"writable"`
}

type CgroupEnvironment struct {
	Version     string                 `json:"version"`
	Controllers map[string]CgroupMount `json:"controllers"`
}

func mountWord(s string) string {
	return strings.NewReplacer(`\040`, " ", `\011`, "\t", `\012`, "\n", `\134`, `\`).Replace(s)
}

// Inspect kernel mount records, including co-mounted v1 controllers and hybrid layouts.
// A cgroup2 mount at /sys/fs/cgroup selects unified mode, as runc does.
func inspectCgroups(mountinfo []byte, requested string, readFile func(string) ([]byte, error), statFile func(string) error) (CgroupEnvironment, error) {
	env := CgroupEnvironment{Version: "v1", Controllers: map[string]CgroupMount{}}
	if requested == "" {
		requested = "auto"
	}
	if requested != "auto" && requested != "v1" && requested != "v2" {
		return env, fmt.Errorf("cgroupMode must be auto, v1 or v2")
	}
	var unified *CgroupMount
	for _, line := range strings.Split(string(mountinfo), "\n") {
		left, right, ok := strings.Cut(line, " - ")
		if !ok {
			continue
		}
		fields, filesystem := strings.Fields(left), strings.Fields(right)
		if len(fields) < 6 || len(filesystem) < 3 {
			continue
		}
		mount := CgroupMount{Root: mountWord(fields[3]), Mountpoint: mountWord(fields[4])}
		mount.Writable = hasWord(fields[5], "rw") && hasWord(filesystem[2], "rw")
		if filesystem[0] == "cgroup2" && path.Clean(mount.Mountpoint) == "/sys/fs/cgroup" {
			copy := mount
			unified = &copy
		}
		if filesystem[0] != "cgroup" {
			continue
		}
		for _, controller := range strings.Split(filesystem[2], ",") {
			// Only the required v1 subsystems are relevant to this runtime.
			if _, ok := v1ControlFiles[controller]; !ok {
				continue
			}
			old, exists := env.Controllers[controller]
			if !exists || (old.Root != "/" && mount.Root == "/") {
				env.Controllers[controller] = mount
			}
		}
	}
	if unified != nil {
		env.Version = "v2"
	}
	if requested != "auto" && requested != env.Version {
		return env, fmt.Errorf("requested cgroup %s but detected %s", requested, env.Version)
	}
	if unified != nil {
		env.Controllers = map[string]CgroupMount{}
		if !unified.Writable {
			return env, fmt.Errorf("cgroup v2 mount is read-only: %s", unified.Mountpoint)
		}
		data, err := readFile(path.Join(unified.Mountpoint, "cgroup.controllers"))
		if err != nil {
			return env, fmt.Errorf("read cgroup v2 controllers: %w", err)
		}
		available := map[string]bool{}
		for _, controller := range strings.Fields(string(data)) {
			available[controller] = true
		}
		for _, controller := range []string{"cpu", "memory", "pids"} {
			if !available[controller] {
				return env, fmt.Errorf("cgroup v2 controller unavailable: %s", controller)
			}
			env.Controllers[controller] = *unified
		}
		return env, nil
	}
	for _, controller := range []string{"cpu", "cpuacct", "memory", "pids", "devices", "freezer"} {
		mount, ok := env.Controllers[controller]
		if !ok {
			return env, fmt.Errorf("cgroup v1 controller is not mounted: %s; inspect findmnt -t cgroup and /proc/cgroups", controller)
		}
		if !mount.Writable {
			return env, fmt.Errorf("cgroup v1 %s mount is read-only: %s", controller, mount.Mountpoint)
		}
		for _, file := range v1ControlFiles[controller] {
			if err := statFile(path.Join(mount.Mountpoint, file)); err != nil {
				return env, fmt.Errorf("cgroup v1 %s control file unavailable (%s): %w", controller, file, err)
			}
		}
	}
	return env, nil
}

var v1ControlFiles = map[string][]string{
	// Limit files (notably pids.max/freezer.state) may be absent at hierarchy
	// roots. runc applies them to newly created child groups; probe root counters.
	"cpu":     {},
	"cpuacct": {"cpuacct.usage"},
	"memory":  {"memory.usage_in_bytes"},
	"pids":    {},
	"devices": {},
	"freezer": {},
}

func hasWord(words, wanted string) bool {
	for _, word := range strings.Split(words, ",") {
		if word == wanted {
			return true
		}
	}
	return false
}

func v1ProcessFile(env CgroupEnvironment, membership []byte, controller, file string) (string, error) {
	mount, ok := env.Controllers[controller]
	if !ok {
		return "", fmt.Errorf("controller unavailable: %s", controller)
	}
	for _, line := range strings.Split(string(membership), "\n") {
		fields := strings.SplitN(line, ":", 3)
		if len(fields) != 3 || !hasWord(fields[1], controller) {
			continue
		}
		group := fields[2]
		if !strings.HasPrefix(group, "/") || path.Clean(group) != group {
			return "", fmt.Errorf("invalid process cgroup path")
		}
		root := path.Clean(mount.Root)
		if root != "/" && group != root && !strings.HasPrefix(group, root+"/") {
			return "", fmt.Errorf("process cgroup is outside mounted root")
		}
		relative := strings.TrimPrefix(strings.TrimPrefix(group, root), "/")
		return path.Join(mount.Mountpoint, relative, file), nil
	}
	return "", fmt.Errorf("process has no cgroup membership for %s", controller)
}

// v1 metrics use the container init PID's actual groups; paths do not assume a
// cgroupfs or systemd naming scheme. Missing/reset counters cannot drive HPA down.
func readV1Metrics(env CgroupEnvironment, membership []byte, previous Metrics, now time.Time, readFile func(string) ([]byte, error)) Metrics {
	read := func(controller, file string) (uint64, error) {
		name, err := v1ProcessFile(env, membership, controller, file)
		if err != nil {
			return 0, err
		}
		data, err := readFile(name)
		if err != nil {
			return 0, err
		}
		return strconv.ParseUint(strings.TrimSpace(string(data)), 10, 64)
	}
	total, err := read("cpuacct", "cpuacct.usage")
	if err != nil {
		return Metrics{}
	}
	memory, err := read("memory", "memory.usage_in_bytes")
	if err != nil || memory > math.MaxInt64 {
		return Metrics{}
	}
	return sampledMetrics(total, int64(memory), previous, now)
}
