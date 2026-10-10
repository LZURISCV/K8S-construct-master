// Static hardware HAL: administrator-declared Linux devices, scheduler accounting
// and exclusive allocation are delegated to the Kubernetes device-plugin API.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	api "k8s.io/kubelet/pkg/apis/deviceplugin/v1beta1"
)

type Device struct {
	ID            string `json:"id"`
	HostPath      string `json:"hostPath"`
	ContainerPath string `json:"containerPath"`
	Permissions   string `json:"permissions"`
	NUMA          *int64 `json:"numa"`
}
type Config struct {
	ResourceName string   `json:"resourceName"`
	Devices      []Device `json:"devices"`
}
type Plugin struct {
	api.UnimplementedDevicePluginServer
	config Config
}

func validate(c Config) error {
	parts := strings.Split(c.ResourceName, "/")
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" || strings.ContainsAny(c.ResourceName, " \n\\") || len(c.Devices) == 0 {
		return errors.New("extended resource and devices required")
	}
	seen := map[string]bool{}
	paths := map[string]bool{}
	for _, d := range c.Devices {
		if d.ID == "" || seen[d.ID] || paths[d.HostPath] || !strings.HasPrefix(d.HostPath, "/dev/") || path.Clean(d.HostPath) != d.HostPath || !strings.HasPrefix(d.ContainerPath, "/dev/") || path.Clean(d.ContainerPath) != d.ContainerPath {
			return errors.New("invalid/duplicate device identity or path")
		}
		if d.Permissions == "" || strings.Trim(d.Permissions, "rwm") != "" || (d.NUMA != nil && *d.NUMA < 0) {
			return errors.New("invalid permissions/NUMA")
		}
		seen[d.ID] = true
		paths[d.HostPath] = true
	}
	return nil
}
func healthy(d Device) bool {
	info, e := os.Stat(d.HostPath)
	return e == nil && info.Mode()&os.ModeDevice != 0
}
func (p *Plugin) GetDevicePluginOptions(context.Context, *api.Empty) (*api.DevicePluginOptions, error) {
	return &api.DevicePluginOptions{}, nil
}
func (p *Plugin) ListAndWatch(_ *api.Empty, stream api.DevicePlugin_ListAndWatchServer) error {
	tick := time.NewTicker(5 * time.Second)
	defer tick.Stop()
	for {
		devices := []*api.Device{}
		for _, d := range p.config.Devices {
			state := api.Unhealthy
			if healthy(d) {
				state = api.Healthy
			}
			record := &api.Device{ID: d.ID, Health: state}
			if d.NUMA != nil {
				record.Topology = &api.TopologyInfo{Nodes: []*api.NUMANode{{ID: *d.NUMA}}}
			}
			devices = append(devices, record)
		}
		if err := stream.Send(&api.ListAndWatchResponse{Devices: devices}); err != nil {
			return err
		}
		select {
		case <-stream.Context().Done():
			return stream.Context().Err()
		case <-tick.C:
		}
	}
}
func (p *Plugin) Allocate(_ context.Context, request *api.AllocateRequest) (*api.AllocateResponse, error) {
	out := &api.AllocateResponse{}
	used := map[string]bool{}
	for _, container := range request.ContainerRequests {
		result := &api.ContainerAllocateResponse{}
		for _, id := range container.DevicesIDs {
			found := false
			for _, d := range p.config.Devices {
				if d.ID == id {
					if used[id] || !healthy(d) {
						return nil, fmt.Errorf("unhealthy/duplicate device %s", id)
					}
					used[id] = true
					found = true
					result.Devices = append(result.Devices, &api.DeviceSpec{HostPath: d.HostPath, ContainerPath: d.ContainerPath, Permissions: d.Permissions})
				}
			}
			if !found {
				return nil, fmt.Errorf("unknown device %s", id)
			}
		}
		out.ContainerResponses = append(out.ContainerResponses, result)
	}
	return out, nil
}
func (p *Plugin) PreStartContainer(context.Context, *api.PreStartContainerRequest) (*api.PreStartContainerResponse, error) {
	return &api.PreStartContainerResponse{}, nil
}
func (p *Plugin) GetPreferredAllocation(context.Context, *api.PreferredAllocationRequest) (*api.PreferredAllocationResponse, error) {
	return &api.PreferredAllocationResponse{}, nil
}
func main() {
	configPath := flag.String("config", "/etc/rv-platform/devices.json", "static administrator hardware configuration")
	directory := flag.String("socket-dir", "/var/lib/kubelet/device-plugins", "kubelet device plugin directory")
	flag.Parse()
	raw, e := os.ReadFile(*configPath)
	if e != nil {
		panic(e)
	}
	var c Config
	if e = json.Unmarshal(raw, &c); e != nil {
		panic(e)
	}
	if e = validate(c); e != nil {
		panic(e)
	}
	endpoint := "rv-" + strings.ReplaceAll(c.ResourceName, "/", "-") + ".sock"
	socket := filepath.Join(*directory, endpoint)
	if len(socket) > 100 {
		panic("Unix socket name too long")
	}
	if e = os.MkdirAll(*directory, 0755); e != nil {
		panic(e)
	}
	if e = os.Remove(socket); e != nil && !os.IsNotExist(e) {
		panic(e)
	}
	listener, e := net.Listen("unix", socket)
	if e != nil {
		panic(e)
	}
	defer listener.Close()
	defer os.Remove(socket)
	if e = os.Chmod(socket, 0600); e != nil {
		panic(e)
	}
	server := grpc.NewServer()
	api.RegisterDevicePluginServer(server, &Plugin{config: c})
	go func() {
		if e := server.Serve(listener); e != nil {
			fmt.Fprintln(os.Stderr, e)
			os.Exit(1)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	connection, e := grpc.DialContext(ctx, "passthrough:///kubelet-registration",
		grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithBlock(),
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", filepath.Join(*directory, "kubelet.sock"))
		}))
	if e != nil {
		panic(e)
	}
	defer connection.Close()
	_, e = api.NewRegistrationClient(connection).Register(ctx, &api.RegisterRequest{Version: api.Version, Endpoint: endpoint, ResourceName: c.ResourceName, Options: &api.DevicePluginOptions{}})
	if e != nil {
		panic(e)
	}
	fmt.Println("registered", c.ResourceName)
	for {
		time.Sleep(3 * time.Second)
		if _, e = os.Stat(socket); e != nil {
			server.Stop()
			return
		} // systemd re-registers after kubelet removes socket
	}
}
