package main

import (
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

func legacyMounts(exclude string) []byte {
	text := "20 1 0:20 / /sys/fs/cgroup rw - tmpfs tmpfs rw\n"
	for i, controllers := range []string{"cpu,cpuacct", "memory", "pids", "devices", "freezer"} {
		if controllers == exclude {
			continue
		}
		text += fmt.Sprintf("%d 20 0:%d / /sys/fs/cgroup/%s rw,nosuid,nodev,noexec - cgroup cgroup rw,%s\n", 30+i, 30+i, controllers, controllers)
	}
	return []byte(text)
}

func rootProbe(name string) error {
	// Only accounting files exist in this fixture, not limit files at roots.
	if strings.HasSuffix(name, "/cpuacct.usage") || strings.HasSuffix(name, "/memory.usage_in_bytes") {
		return nil
	}
	return os.ErrNotExist
}

func TestCgroupV1CoMountedControllers(t *testing.T) {
	env, err := inspectCgroups(legacyMounts(""), "auto", nil, rootProbe)
	if err != nil || env.Version != "v1" || env.Controllers["cpu"].Mountpoint != env.Controllers["cpuacct"].Mountpoint {
		t.Fatalf("co-mounted v1 detection: %+v %v", env, err)
	}
}

func TestCgroupV1HybridUsesLegacyMounts(t *testing.T) {
	data := append(legacyMounts(""), []byte("50 20 0:50 / /sys/fs/cgroup/unified rw - cgroup2 cgroup rw\n")...)
	env, err := inspectCgroups(data, "v1", nil, rootProbe)
	if err != nil || env.Version != "v1" {
		t.Fatalf("hybrid detection: %+v %v", env, err)
	}
}

func TestCgroupDetectionRejectsIncompleteV1(t *testing.T) {
	for _, missing := range []string{"cpu,cpuacct", "memory", "pids", "devices", "freezer"} {
		_, err := inspectCgroups(legacyMounts(missing), "auto", nil, rootProbe)
		if err == nil || !strings.Contains(err.Error(), "not mounted") {
			t.Fatalf("missing %s accepted: %v", missing, err)
		}
	}
}

func TestCgroupV2DetectionAndRequiredControllers(t *testing.T) {
	data := []byte("20 1 0:20 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
	read := func(string) ([]byte, error) { return []byte("cpu memory pids io"), nil }
	env, err := inspectCgroups(data, "auto", read, nil)
	if err != nil || env.Version != "v2" {
		t.Fatalf("v2 detection: %+v %v", env, err)
	}
	read = func(string) ([]byte, error) { return []byte("cpu pids"), nil }
	if _, err = inspectCgroups(data, "auto", read, nil); err == nil || !strings.Contains(err.Error(), "memory") {
		t.Fatal("missing v2 memory accepted")
	}
}

func TestCgroupModeMismatchAndInvalidMode(t *testing.T) {
	for _, mode := range []string{"v2", "typo"} {
		if _, err := inspectCgroups(legacyMounts(""), mode, nil, rootProbe); err == nil {
			t.Fatalf("mode %s accepted", mode)
		}
	}
	if _, err := inspectCgroups([]byte("20 1 0:20 / /sys/fs/cgroup rw - cgroup2 cgroup rw"), "v1", nil, nil); err == nil {
		t.Fatal("v1 override silently selected v2")
	}
}

func TestCgroupRejectsReadOnlyAndUnavailableCounters(t *testing.T) {
	data := strings.ReplaceAll(string(legacyMounts("")), " rw,nosuid", " ro,nosuid")
	if _, err := inspectCgroups([]byte(data), "v1", nil, rootProbe); err == nil || !strings.Contains(err.Error(), "read-only") {
		t.Fatal("read-only v1 accepted")
	}
	if _, err := inspectCgroups(legacyMounts(""), "v1", nil, func(string) error { return os.ErrNotExist }); err == nil {
		t.Fatal("missing accounting files accepted")
	}
}

func TestV1ProcessPathMergedAndSystemd(t *testing.T) {
	env, _ := inspectCgroups(legacyMounts(""), "auto", nil, rootProbe)
	for _, group := range []string{"/rcs/rcs-000000000001", "/rcs.slice/rcs-000000000001.scope"} {
		membership := []byte("4:cpu,cpuacct:" + group + "\n3:memory:" + group + "\n")
		name, err := v1ProcessFile(env, membership, "cpuacct", "cpuacct.usage")
		if err != nil || name != "/sys/fs/cgroup/cpu,cpuacct"+group+"/cpuacct.usage" {
			t.Fatalf("process group: %s %v", name, err)
		}
	}
}

func TestV1ProcessPathMountRootAndEscapes(t *testing.T) {
	env := CgroupEnvironment{Controllers: map[string]CgroupMount{"cpuacct": {Root: "/parent", Mountpoint: mountWord(`/sys/fs/cgroup/cpu\040account`)}}}
	name, err := v1ProcessFile(env, []byte("4:cpuacct:/parent/rcs/pod\n"), "cpuacct", "cpuacct.usage")
	if err != nil || name != "/sys/fs/cgroup/cpu account/rcs/pod/cpuacct.usage" {
		t.Fatalf("root/escape handling: %s %v", name, err)
	}
	for _, group := range []string{"/different/pod", "/parent/../other", "relative"} {
		if _, err := v1ProcessFile(env, []byte("4:cpuacct:"+group), "cpuacct", "cpuacct.usage"); err == nil {
			t.Fatalf("invalid path accepted: %s", group)
		}
	}
}

func TestV1MetricsUnitsAndInvalidSamples(t *testing.T) {
	env, _ := inspectCgroups(legacyMounts(""), "v1", nil, rootProbe)
	membership := []byte("4:cpu,cpuacct:/rcs/pod\n3:memory:/rcs/pod\n")
	read := func(name string) ([]byte, error) {
		if strings.HasSuffix(name, "cpuacct.usage") {
			return []byte("2500000000\n"), nil
		}
		return []byte("67108864\n"), nil
	}
	previous := Metrics{total: 1000000000, sample: testNow}
	m := readV1Metrics(env, membership, previous, testNow.Add(2*time.Second), read)
	if !m.Valid || m.CPUUsageMillis != 750 || m.MemoryUsageBytes != 64<<20 {
		t.Fatalf("v1 units: %+v", m)
	}
	if readV1Metrics(env, membership, Metrics{}, testNow, read).Valid {
		t.Fatal("first sample used for HPA")
	}
	previous.total = 3000000000
	if readV1Metrics(env, membership, previous, testNow.Add(time.Second), read).Valid {
		t.Fatal("reset CPU counter accepted")
	}
	if readV1Metrics(env, membership, previous, testNow, func(string) ([]byte, error) { return nil, os.ErrNotExist }).Valid {
		t.Fatal("missing metrics accepted")
	}
	if readV1Metrics(env, membership, previous, testNow, func(string) ([]byte, error) { return []byte("-1"), nil }).Valid {
		t.Fatal("negative counter accepted")
	}
	if readV1Metrics(env, nil, previous, testNow.Add(time.Second), read).Valid {
		t.Fatal("missing process membership accepted")
	}
}
