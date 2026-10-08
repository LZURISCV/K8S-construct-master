package main

import (
	"sort"
	"strings"
)

func ociConfig(p Pod, c AgentConfig) map[string]any {
	env := []string{"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root", "RCS_POD_ID=" + p.ID, "RCS_NODE_ID=" + p.NodeID}
	keys := []string{}
	for k := range p.Spec.Env {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		env = append(env, k+"="+p.Spec.Env[k])
	}
	caps := []string{"CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FSETID", "CAP_FOWNER", "CAP_SETGID", "CAP_SETUID", "CAP_SETFCAP", "CAP_KILL", "CAP_NET_BIND_SERVICE", "CAP_SYS_CHROOT"}
	cgroup := "/rcs/" + p.ID
	if c.SystemdCgroup {
		cgroup = "rcs.slice:rcs:" + strings.TrimPrefix(p.ID, "rcs-")
	}
	return map[string]any{
		"ociVersion": "1.0.2", "hostname": p.ID,
		"root":    map[string]any{"path": "rootfs", "readonly": false},
		"process": map[string]any{"terminal": false, "user": map[string]int{"uid": 0, "gid": 0}, "args": p.Spec.Command, "env": env, "cwd": "/", "noNewPrivileges": true, "capabilities": map[string]any{"bounding": caps, "effective": caps, "permitted": caps}, "rlimits": []any{map[string]any{"type": "RLIMIT_NOFILE", "hard": 65536, "soft": 65536}}},
		"mounts": []any{
			map[string]any{"destination": "/proc", "type": "proc", "source": "proc"},
			map[string]any{"destination": "/dev", "type": "tmpfs", "source": "tmpfs", "options": []string{"nosuid", "strictatime", "mode=755", "size=65536k"}},
			map[string]any{"destination": "/dev/pts", "type": "devpts", "source": "devpts", "options": []string{"nosuid", "noexec", "newinstance", "ptmxmode=0666", "mode=0620", "gid=5"}},
			map[string]any{"destination": "/dev/shm", "type": "tmpfs", "source": "shm", "options": []string{"nosuid", "noexec", "nodev", "mode=1777", "size=67108864"}},
			map[string]any{"destination": "/dev/mqueue", "type": "mqueue", "source": "mqueue", "options": []string{"nosuid", "noexec", "nodev"}},
			map[string]any{"destination": "/sys", "type": "sysfs", "source": "sysfs", "options": []string{"nosuid", "noexec", "nodev", "ro"}},
		},
		"linux": map[string]any{
			"cgroupsPath": cgroup,
			// Omit network namespace: reachable host IPs, declared host-port reservations.
			"namespaces":    []any{map[string]string{"type": "pid"}, map[string]string{"type": "ipc"}, map[string]string{"type": "uts"}, map[string]string{"type": "mount"}},
			"resources":     map[string]any{"memory": map[string]any{"limit": p.Spec.Limits.MemoryBytes}, "cpu": map[string]any{"period": 100000, "quota": p.Spec.Limits.CPUMillis * 100}, "pids": map[string]any{"limit": 4096}},
			"maskedPaths":   []string{"/proc/acpi", "/proc/asound", "/proc/kcore", "/proc/keys", "/proc/latency_stats", "/proc/timer_list", "/proc/timer_stats", "/proc/sched_debug", "/sys/firmware"},
			"readonlyPaths": []string{"/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger"},
		},
	}
}
