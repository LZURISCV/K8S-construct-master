//go:build !linux

package main

import "fmt"

func syncDirectory(path string) error { return nil }
func machineCapacity() (Resources, string, error) {
	return Resources{}, "", fmt.Errorf("runc agent requires Linux")
}
func newRuncRuntime(c AgentConfig) (Runtime, error) {
	return nil, fmt.Errorf("runc agent requires Linux")
}
func checkHostCgroups(mode string) (CgroupEnvironment, error) {
	return CgroupEnvironment{}, fmt.Errorf("cgroup checks require Linux")
}
