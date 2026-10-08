//go:build linux

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func syncDirectory(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}
func machineCapacity() (Resources, string, error) {
	b, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return Resources{}, "", err
	}
	var kb int64
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, "MemTotal:") {
			fields := strings.Fields(line)
			kb, _ = strconv.ParseInt(fields[1], 10, 64)
		}
	}
	if kb <= 0 {
		return Resources{}, "", fmt.Errorf("cannot read physical memory")
	}
	return Resources{int64(runtime.NumCPU()) * 1000, kb * 1024}, runtime.GOARCH, nil
}

type RuncRuntime struct {
	config  AgentConfig
	cgroups CgroupEnvironment
}

func checkHostCgroups(mode string) (CgroupEnvironment, error) {
	data, err := os.ReadFile("/proc/self/mountinfo")
	if err != nil {
		return CgroupEnvironment{}, err
	}
	return inspectCgroups(data, mode, os.ReadFile, func(name string) error { _, err := os.Stat(name); return err })
}

func newRuncRuntime(c AgentConfig) (Runtime, error) {
	if os.Geteuid() != 0 {
		return nil, fmt.Errorf("agent requires root for runc namespaces and cgroups")
	}
	if _, err := exec.LookPath(c.Runc); err != nil {
		return nil, err
	}
	cgroups, err := checkHostCgroups(c.CgroupMode)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(c.RuncRoot, 0700); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Join(c.DataDir, "pods"), 0700); err != nil {
		return nil, err
	}
	log.Printf("runc environment: cgroup=%s systemdCgroup=%t", cgroups.Version, c.SystemdCgroup)
	return &RuncRuntime{config: c, cgroups: cgroups}, nil
}
func (r *RuncRuntime) CgroupVersion() string { return r.cgroups.Version }
func (r *RuncRuntime) args(args ...string) []string {
	v := []string{"--root", r.config.RuncRoot}
	if r.config.SystemdCgroup {
		v = append(v, "--systemd-cgroup")
	}
	return append(v, args...)
}
func (r *RuncRuntime) dir(id string) string { return filepath.Join(r.config.DataDir, "pods", id) }
func (r *RuncRuntime) command(ctx context.Context, args ...string) *exec.Cmd {
	return exec.CommandContext(ctx, r.config.Runc, r.args(args...)...)
}
func (r *RuncRuntime) Inspect(id string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	out, err := r.command(ctx, "list", "--format", "json").Output()
	if err != nil {
		return "", fmt.Errorf("runc list: %w", err)
	}
	var states []struct {
		ID     string `json:"id"`
		Status string `json:"status"`
	}
	if err = json.Unmarshal(out, &states); err != nil {
		return "", err
	}
	for _, s := range states {
		if s.ID == id {
			return s.Status, nil
		}
	}
	return "absent", nil
}
func (r *RuncRuntime) Start(p Pod) (<-chan processResult, error) {
	base := r.config.Images[p.Spec.Image]
	if base == "" {
		return nil, fmt.Errorf("image %s unavailable", p.Spec.Image)
	}
	dir := r.dir(p.ID)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	// A fresh assignment has a globally unique ID; partial prepare is never reused.
	staging := filepath.Join(dir, "rootfs-preparing")
	rootfs := filepath.Join(dir, "rootfs")
	if err := os.Mkdir(staging, 0755); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	copy := exec.CommandContext(ctx, "cp", "-a", "--reflink=auto", base+"/.", staging+"/")
	if out, err := copy.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("copy rootfs: %w: %s", err, out)
	}
	if err := os.Rename(staging, rootfs); err != nil {
		return nil, err
	}
	// Resolve DNS inside each private filesystem without exposing a host writable mount.
	if data, err := os.ReadFile("/etc/resolv.conf"); err == nil {
		dest := filepath.Join(rootfs, "etc", "resolv.conf")
		_ = os.MkdirAll(filepath.Dir(dest), 0755)
		_ = os.Remove(dest)
		if err = os.WriteFile(dest, data, 0644); err != nil {
			return nil, err
		}
	}
	if err := atomicJSON(filepath.Join(dir, "config.json"), ociConfig(p, r.config)); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(filepath.Join(dir, "container.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return nil, err
	}
	cmd := exec.Command(r.config.Runc, r.args("run", "--bundle", dir, p.ID)...)
	cmd.Stdout = file
	cmd.Stderr = file
	if err = cmd.Start(); err != nil {
		file.Close()
		return nil, err
	}
	done := make(chan processResult, 1)
	go func() {
		err := cmd.Wait()
		_ = file.Close()
		code := 0
		if err != nil {
			code = -1
			if x, ok := err.(*exec.ExitError); ok {
				code = x.ExitCode()
			}
		}
		done <- processResult{code, err}
	}()
	// Running means runc has actually started init, rather than merely starting the CLI process.
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case result := <-done:
			done <- result
			return done, nil
		default:
		}
		state, err := r.Inspect(p.ID)
		if err != nil {
			if stopErr := r.Stop(p.ID); stopErr != nil {
				return nil, fmt.Errorf("%w: %v", errRuntimeUncertain, stopErr)
			}
			return nil, err
		}
		if state == "running" {
			return done, nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	if err := r.Stop(p.ID); err != nil {
		return nil, fmt.Errorf("%w: %v", errRuntimeUncertain, err)
	}
	return nil, fmt.Errorf("runc startup timed out")
}
func (r *RuncRuntime) Stop(id string) error {
	state, err := r.Inspect(id)
	if err != nil {
		return err
	}
	if state == "absent" {
		return nil
	}
	if state == "running" || state == "paused" {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		out, killErr := r.command(ctx, "kill", id, "TERM").CombinedOutput()
		cancel()
		if killErr != nil {
			state, err = r.Inspect(id)
			if err != nil {
				return err
			}
			if state == "running" || state == "paused" {
				return fmt.Errorf("runc kill: %w: %s", killErr, out)
			}
		}
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		state, err = r.Inspect(id)
		if err != nil {
			return err
		}
		if state == "stopped" || state == "absent" {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	out, err := r.command(ctx, "delete", "--force", id).CombinedOutput()
	if err != nil {
		state, stateErr := r.Inspect(id)
		if stateErr == nil && state == "absent" {
			return nil
		}
		return fmt.Errorf("runc delete: %w: %s", err, out)
	}
	return nil
}
func (r *RuncRuntime) Stats(id string, previous Metrics) Metrics {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if r.cgroups.Version == "v1" {
		out, err := r.command(ctx, "state", id).Output()
		if err != nil {
			return Metrics{}
		}
		var state struct {
			PID    int    `json:"pid"`
			Status string `json:"status"`
		}
		if json.Unmarshal(out, &state) != nil || state.PID <= 0 || state.Status != "running" {
			return Metrics{}
		}
		membership, err := os.ReadFile(fmt.Sprintf("/proc/%d/cgroup", state.PID))
		if err != nil {
			return Metrics{}
		}
		return readV1Metrics(r.cgroups, membership, previous, time.Now(), os.ReadFile)
	}
	out, err := r.command(ctx, "events", "--stats", id).Output()
	if err != nil {
		return Metrics{}
	}
	return parseRuncStats(out, previous, time.Now())
}

type cappedBuffer struct {
	data  []byte
	limit int
}

func (b *cappedBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if len(b.data) < b.limit {
		keep := b.limit - len(b.data)
		if keep > n {
			keep = n
		}
		b.data = append(b.data, p[:keep]...)
	}
	return n, nil
}
func (r *RuncRuntime) Exec(ctx context.Context, id string, args []string) (string, int) {
	if len(args) == 0 {
		return "empty command", -1
	}
	b := &cappedBuffer{limit: 65536}
	cmd := r.command(ctx, append([]string{"exec", id}, args...)...)
	cmd.Stdout = b
	cmd.Stderr = b
	err := cmd.Run()
	if err != nil {
		code := -1
		if x, ok := err.(*exec.ExitError); ok {
			code = x.ExitCode()
		}
		return string(b.data) + "\n" + err.Error(), code
	}
	return string(b.data), 0
}
func (r *RuncRuntime) Logs(id string) string {
	f, err := os.Open(filepath.Join(r.dir(id), "container.log"))
	if err != nil {
		return ""
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return ""
	}
	if info.Size() > 32768 {
		_, _ = f.Seek(-32768, io.SeekEnd)
	}
	b, _ := io.ReadAll(io.LimitReader(f, 32768))
	return string(b)
}
func (r *RuncRuntime) Remove(id string) error {
	if !validPodID(id) {
		return fmt.Errorf("unsafe pod path")
	}
	return os.RemoveAll(r.dir(id))
}
