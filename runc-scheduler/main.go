package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var version = "dev"

func main() {
	if err := run(os.Args[1:]); err != nil {
		log.Print(err)
		os.Exit(1)
	}
}
func run(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: rcs control|agent|ctl|stress|token|version|check-cgroups")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	switch args[0] {
	case "version":
		fmt.Printf("rcs %s %s/%s\n", version, runtime.GOOS, runtime.GOARCH)
		return nil
	case "check-cgroups":
		fs := flag.NewFlagSet("check-cgroups", flag.ContinueOnError)
		mode := fs.String("mode", "auto", "expected cgroup mode: auto, v1, v2")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if len(fs.Args()) != 0 {
			return fmt.Errorf("usage: rcs check-cgroups [-mode auto|v1|v2]")
		}
		env, err := checkHostCgroups(*mode)
		if err != nil {
			return err
		}
		return printJSON(env)
	case "token":
		b := make([]byte, 32)
		if _, err := rand.Read(b); err != nil {
			return err
		}
		fmt.Println(hex.EncodeToString(b))
		return nil
	case "ctl":
		return runCLI(ctx, args[1:])
	case "stress":
		return runStress(ctx, args[1:])
	case "marker":
		if len(args) != 2 || (args[1] != "on" && args[1] != "off") {
			return fmt.Errorf("usage: rcs marker on|off")
		}
		if args[1] == "on" {
			return os.WriteFile("/tmp/enable_pressure", []byte("1"), 0600)
		}
		if err := os.Remove("/tmp/enable_pressure"); err != nil && !os.IsNotExist(err) {
			return err
		}
		return nil
	case "control", "agent":
		fs := flag.NewFlagSet(args[0], flag.ContinueOnError)
		config := fs.String("config", "", "JSON config file")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if *config == "" {
			return fmt.Errorf("-config required")
		}
		if args[0] == "control" {
			var c ControlConfig
			if err := readJSON(*config, &c); err != nil {
				return err
			}
			return runControl(ctx, c)
		}
		var c AgentConfig
		if err := readJSON(*config, &c); err != nil {
			return err
		}
		a, err := newAgent(c)
		if err != nil {
			return err
		}
		return a.run(ctx)
	default:
		return fmt.Errorf("unknown command %s", args[0])
	}
}
func printJSON(v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(b))
	return nil
}
func runCLI(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("ctl", flag.ContinueOnError)
	url := fs.String("url", os.Getenv("RCS_URL"), "control URL")
	tokenFile := fs.String("token-file", os.Getenv("RCS_TOKEN_FILE"), "admin token file")
	ca := fs.String("ca", os.Getenv("RCS_CA_FILE"), "CA file")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *url == "" {
		*url = "http://127.0.0.1:8080"
	}
	token, err := readToken(*tokenFile)
	if err != nil {
		return err
	}
	c, err := newClient(*url, token, *ca)
	if err != nil {
		return err
	}
	a := fs.Args()
	if len(a) == 0 {
		return fmt.Errorf("ctl: apply|get|delete|scale|label|taint|untaint|cordon|uncordon|exec|logs|wait")
	}
	get := func() (State, error) { var s State; err := c.request(ctx, "GET", "/v1/state", nil, &s); return s, err }
	ack := func(method, path string, v any) error {
		var out any
		if err := c.request(ctx, method, path, v, &out); err != nil {
			return err
		}
		return printJSON(out)
	}
	switch a[0] {
	case "get":
		s, err := get()
		if err != nil {
			return err
		}
		kind := "all"
		if len(a) > 1 {
			kind = a[1]
		}
		switch kind {
		case "nodes":
			return printJSON(s.Nodes)
		case "pods":
			return printJSON(s.Pods)
		case "workloads":
			return printJSON(s.Workloads)
		case "events":
			return printJSON(s.Events)
		case "all":
			return printJSON(s)
		default:
			return fmt.Errorf("unknown resource")
		}
	case "apply":
		if len(a) != 2 {
			return fmt.Errorf("ctl apply FILE.json")
		}
		var w Workload
		if err := readJSON(a[1], &w); err != nil {
			return err
		}
		return ack("POST", "/v1/workloads", w)
	case "delete":
		if len(a) != 2 {
			return fmt.Errorf("ctl delete WORKLOAD")
		}
		return ack("DELETE", "/v1/workloads/"+a[1], nil)
	case "scale":
		if len(a) != 3 {
			return fmt.Errorf("ctl scale WORKLOAD COUNT")
		}
		n, err := strconv.Atoi(a[2])
		if err != nil {
			return err
		}
		return ack("POST", "/v1/scale/"+a[1], map[string]int{"replicas": n})
	case "label":
		if len(a) != 3 {
			return fmt.Errorf("ctl label NODE KEY=VALUE or KEY-")
		}
		p := NodePatch{Labels: map[string]*string{}}
		if strings.HasSuffix(a[2], "-") {
			p.Labels[strings.TrimSuffix(a[2], "-")] = nil
		} else {
			k, v, ok := strings.Cut(a[2], "=")
			if !ok || k == "" {
				return fmt.Errorf("label requires key=value")
			}
			p.Labels[k] = &v
		}
		return ack("PATCH", "/v1/nodes/"+a[1], p)
	case "cordon", "uncordon":
		if len(a) != 2 {
			return fmt.Errorf("ctl cordon|uncordon NODE")
		}
		yes := a[0] == "cordon"
		return ack("PATCH", "/v1/nodes/"+a[1], NodePatch{Cordoned: &yes})
	case "taint", "untaint":
		if len(a) != 3 {
			return fmt.Errorf("ctl taint NODE key=value:NoSchedule; ctl untaint NODE key")
		}
		s, err := get()
		if err != nil {
			return err
		}
		n := s.Nodes[a[1]]
		if n == nil {
			return fmt.Errorf("node not found")
		}
		ts := []Taint{}
		key := a[2]
		var t Taint
		if a[0] == "taint" {
			kv, effect, ok := strings.Cut(a[2], ":")
			if !ok {
				return fmt.Errorf("taint effect required")
			}
			k, v, _ := strings.Cut(kv, "=")
			t = Taint{k, v, effect}
			key = k
		}
		for _, old := range n.Taints {
			if old.Key != key {
				ts = append(ts, old)
			}
		}
		if a[0] == "taint" {
			ts = append(ts, t)
		}
		return ack("PATCH", "/v1/nodes/"+a[1], NodePatch{Taints: &ts})
	case "logs":
		if len(a) != 2 {
			return fmt.Errorf("ctl logs POD_ID")
		}
		s, err := get()
		if err != nil {
			return err
		}
		p := s.Pods[a[1]]
		if p == nil {
			return fmt.Errorf("pod not found")
		}
		fmt.Print(p.Log)
		return nil
	case "exec":
		if len(a) < 3 {
			return fmt.Errorf("ctl exec POD_ID -- COMMAND ARGS...")
		}
		cmd := a[2:]
		if cmd[0] == "--" {
			cmd = cmd[1:]
		}
		var out struct {
			ID string `json:"id"`
		}
		if err := c.request(ctx, "POST", "/v1/exec", map[string]any{"podId": a[1], "command": cmd}, &out); err != nil {
			return err
		}
		deadline := time.Now().Add(100 * time.Second)
		for time.Now().Before(deadline) {
			s, err := get()
			if err != nil {
				return err
			}
			op := s.Operations[out.ID]
			if op != nil && op.Status == "Done" {
				fmt.Print(op.Output)
				if op.ExitCode != 0 {
					return fmt.Errorf("remote exit code %d", op.ExitCode)
				}
				return nil
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Second):
			}
		}
		return fmt.Errorf("exec wait timed out")
	case "wait":
		if len(a) < 2 || len(a) > 3 {
			return fmt.Errorf("ctl wait WORKLOAD [TIMEOUT_SECONDS]")
		}
		timeout := 180
		if len(a) == 3 {
			timeout, err = strconv.Atoi(a[2])
			if err != nil {
				return err
			}
		}
		deadline := time.Now().Add(time.Duration(timeout) * time.Second)
		for time.Now().Before(deadline) {
			s, err := get()
			if err != nil {
				return err
			}
			w := s.Workloads[a[1]]
			if w == nil {
				return fmt.Errorf("workload not found")
			}
			count := 0
			for _, p := range s.Pods {
				if p.Workload != w.Name || p.Generation != w.Generation {
					continue
				}
				if w.Kind == "job" && p.Phase == "Failed" {
					return fmt.Errorf("job failed: %s\n%s", p.Reason, p.Log)
				}
				if w.Kind == "job" && p.Phase == "Succeeded" || w.Kind == "deployment" && p.Phase == "Running" && s.Nodes[p.NodeID] != nil && s.Nodes[p.NodeID].Ready {
					count++
				}
			}
			if count >= w.Replicas {
				return printJSON(map[string]any{"workload": w.Name, "ready": count})
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Second):
			}
		}
		return fmt.Errorf("workload wait timed out; inspect get pods and get events")
	default:
		return fmt.Errorf("unknown ctl command")
	}
}
func runStress(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("stress", flag.ContinueOnError)
	idle := fs.Int("idle-mib", 16, "idle allocated MiB")
	busy := fs.Int("busy-mib", 160, "pressure allocated MiB")
	trigger := fs.String("trigger", "/tmp/enable_pressure", "pressure marker file")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *idle < 1 || *busy < *idle || *busy > 32768 {
		return fmt.Errorf("invalid memory sizes")
	}
	var memory []byte
	size := -1
	tick := time.NewTicker(500 * time.Millisecond)
	defer tick.Stop()
	for {
		_, err := os.Stat(*trigger)
		want := *idle
		if err == nil {
			want = *busy
		}
		if want != size {
			memory = make([]byte, want*1024*1024)
			for i := 0; i < len(memory); i += 4096 {
				memory[i] = byte(i / 4096)
			}
			size = want
			debug.FreeOSMemory()
			fmt.Printf("%s memory pressure=%t allocatedMiB=%d\n", time.Now().UTC().Format(time.RFC3339), err == nil, want)
		}
		runtime.KeepAlive(memory)
		select {
		case <-ctx.Done():
			return nil
		case <-tick.C:
		}
	}
}
