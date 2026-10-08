package main

import (
	"encoding/json"
	"testing"
)

func TestOCIResourceLimitsAndNamespaces(t *testing.T) {
	w := testWorkload("test", 1, 500)
	p := Pod{ID: "rcs-000000000001", NodeID: "worker-a", Spec: w.Template}
	b, err := json.Marshal(ociConfig(p, AgentConfig{}))
	if err != nil {
		t.Fatal(err)
	}
	var config struct {
		Root  struct{ Path string }
		Linux struct {
			Resources struct {
				CPU struct {
					Quota  int64
					Period uint64
				}
				Memory struct{ Limit int64 }
			}
			Namespaces []struct{ Type string }
		}
	}
	if err = json.Unmarshal(b, &config); err != nil {
		t.Fatal(err)
	}
	if config.Root.Path != "rootfs" || config.Linux.Resources.CPU.Quota != 50000 || config.Linux.Resources.CPU.Period != 100000 || config.Linux.Resources.Memory.Limit != w.Template.Limits.MemoryBytes {
		t.Fatal("invalid OCI limits")
	}
	for _, n := range config.Linux.Namespaces {
		if n.Type == "network" {
			t.Fatal("host networking unexpectedly isolated")
		}
	}
}
