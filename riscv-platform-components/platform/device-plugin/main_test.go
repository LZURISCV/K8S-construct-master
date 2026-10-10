package main

import "testing"

func TestConfig(t *testing.T) {
	c := Config{ResourceName: "hardware.riscv.io/accelerator", Devices: []Device{{ID: "a", HostPath: "/dev/rv0", ContainerPath: "/dev/rv0", Permissions: "rw"}}}
	if validate(c) != nil {
		t.Fatal("valid config rejected")
	}
	c.Devices = append(c.Devices, c.Devices[0])
	if validate(c) == nil {
		t.Fatal("duplicate allowed")
	}
	c.Devices = c.Devices[:1]
	c.Devices[0].HostPath = "/dev/../etc/shadow"
	if validate(c) == nil {
		t.Fatal("traversal allowed")
	}
}
