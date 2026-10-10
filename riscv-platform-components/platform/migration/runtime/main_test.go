package main

import (
	"archive/tar"
	"context"
	"encoding/json"
	"io"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	meta "github.com/checkpoint-restore/checkpointctl/lib"
	"google.golang.org/grpc"
	cri "k8s.io/cri-api/pkg/apis/runtime/v1"
)

func archiveFixture(t *testing.T, id string) manifest {
	t.Helper()
	root = t.TempDir()
	d, e := dir(id)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.MkdirAll(d, 0700); e != nil {
		t.Fatal(e)
	}
	f, e := os.Create(filepath.Join(d, "checkpoint.tar"))
	if e != nil {
		t.Fatal(e)
	}
	tw := tar.NewWriter(f)
	status := cri.ContainerStatus{Metadata: &cri.ContainerMetadata{Name: "counter"}, Labels: map[string]string{"io.kubernetes.pod.namespace": "demo", "io.kubernetes.pod.uid": "source-uid"}}
	b, _ := json.Marshal(status)
	for name, data := range map[string][]byte{statusDumpFile: b, meta.ConfigDumpFile: []byte("{}"), meta.SpecDumpFile: []byte("{}"), meta.RootFsDiffTar: []byte("data"), "checkpoint/inventory.img": []byte("memory")} {
		if e = addBytes(tw, name, data); e != nil {
			t.Fatal(e)
		}
	}
	if e = tw.Close(); e != nil {
		t.Fatal(e)
	}
	f.Close()
	h, n, e := checksum(f.Name())
	if e != nil {
		t.Fatal(e)
	}
	m := manifest{Format: format, ID: id, Namespace: "demo", PodUID: "source-uid", Container: "counter", SHA256: h, Bytes: n}
	if e = writeJSON(filepath.Join(d, "manifest.json"), m); e != nil {
		t.Fatal(e)
	}
	return m
}
func TestArchiveSourceIdentity(t *testing.T) {
	m := archiveFixture(t, "migration-1")
	d, _ := dir(m.ID)
	p := filepath.Join(d, "checkpoint.tar")
	if e := validateArchive(p, "demo", "source-uid", "counter"); e != nil {
		t.Fatal(e)
	}
	if e := validateArchive(p, "other", "source-uid", "counter"); e == nil {
		t.Fatal("cross-namespace archive accepted")
	}
	if e := validateArchive(p, "demo", "wrong", "counter"); e == nil {
		t.Fatal("wrong source UID accepted")
	}
}
func TestRestoreBoundToExactPodUIDAndChecksum(t *testing.T) {
	m := archiveFixture(t, "migration-2")
	d, _ := dir(m.ID)
	g := grant{ID: m.ID, Namespace: "demo", Pod: "restored", PodUID: "target-uid", Container: "counter", SHA256: m.SHA256, Expires: time.Now().Add(time.Hour).Unix()}
	if e := writeJSON(filepath.Join(d, "grant.json"), g); e != nil {
		t.Fatal(e)
	}
	request := func(uid string) *cri.CreateContainerRequest {
		return &cri.CreateContainerRequest{Config: &cri.ContainerConfig{Metadata: &cri.ContainerMetadata{Name: "counter"}, Image: &cri.ImageSpec{Image: "base@sha256:test"}}, SandboxConfig: &cri.PodSandboxConfig{Metadata: &cri.PodSandboxMetadata{Name: "restored", Namespace: "demo", Uid: uid}, Annotations: map[string]string{annotation: m.ID}}}
	}
	if e := rewrite(request("wrong-uid")); e == nil {
		t.Fatal("forged Pod UID accepted")
	}
	r := request("target-uid")
	if e := rewrite(r); e != nil {
		t.Fatal(e)
	}
	if r.Config.Image.Image != filepath.Join(d, "checkpoint.tar") {
		t.Fatal("restore path was not rewritten")
	}
	f, _ := os.OpenFile(filepath.Join(d, "checkpoint.tar"), os.O_APPEND|os.O_WRONLY, 0600)
	f.Write([]byte("corrupt"))
	f.Close()
	if e := rewrite(request("target-uid")); e == nil {
		t.Fatal("corrupt checkpoint accepted")
	}
}
func TestNormalCRIRequestIsNotRewritten(t *testing.T) {
	r := &cri.CreateContainerRequest{Config: &cri.ContainerConfig{Image: &cri.ImageSpec{Image: "normal:tag"}}}
	if e := rewrite(r); e != nil {
		t.Fatal(e)
	}
	if r.Config.Image.Image != "normal:tag" {
		t.Fatal("ordinary image changed")
	}
}
func TestPathTraversalRejected(t *testing.T) {
	root = t.TempDir()
	for _, id := range []string{"../etc", "/absolute", "a/b", "a\\b", ""} {
		if _, e := dir(id); e == nil {
			t.Fatalf("unsafe ID accepted: %q", id)
		}
	}
	for _, name := range []string{"../secret", "/etc/passwd", "a/../../etc", "a\\b"} {
		if safeMember(name) {
			t.Fatalf("unsafe member: %q", name)
		}
	}
}

type fakeCRI struct {
	cri.UnimplementedRuntimeServiceServer
}

func (fakeCRI) Version(context.Context, *cri.VersionRequest) (*cri.VersionResponse, error) {
	return &cri.VersionResponse{Version: "0.1.0", RuntimeName: "mock-containerd", RuntimeVersion: "2.1.5", RuntimeApiVersion: "v1"}, nil
}
func TestProxyForwardsRealGRPCVersionRPC(t *testing.T) {
	// An actual Unix-socket gRPC exchange, with a fake upstream runtime.
	// On Windows keep socket paths short enough for sockaddr_un.
	d, e := os.MkdirTemp("", "rv-proxy-")
	if e != nil {
		t.Fatal(e)
	}
	defer os.RemoveAll(d)
	upstream = filepath.Join(d, "up.sock")
	socket := filepath.Join(d, "proxy.sock")
	l, e := net.Listen("unix", upstream)
	if e != nil {
		t.Skipf("Unix sockets unavailable: %v", e)
	}
	server := grpc.NewServer()
	cri.RegisterRuntimeServiceServer(server, &fakeCRI{})
	go server.Serve(l)
	defer server.Stop()
	go func() { _ = serve(socket) }()
	for i := 0; i < 100; i++ {
		if _, e = os.Stat(socket); e == nil {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	cc, e := connect(socket)
	if e != nil {
		t.Fatal(e)
	}
	defer cc.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	response, e := cri.NewRuntimeServiceClient(cc).Version(ctx, &cri.VersionRequest{Version: "0.1.0"})
	if e != nil {
		t.Fatal(e)
	}
	if response.RuntimeName != "mock-containerd" {
		t.Fatal("runtime response not forwarded")
	}
}
func TestImportDoesNotPublishCorruptTransfer(t *testing.T) {
	m := archiveFixture(t, "migration-3")
	d, _ := dir(m.ID)
	original := os.Stdin
	defer func() { os.Stdin = original }()
	r, w, e := os.Pipe()
	if e != nil {
		t.Fatal(e)
	}
	os.Stdin = r
	go func() { io.WriteString(w, "truncated"); w.Close() }()
	defer r.Close()
	if e = importArchive(m); e == nil {
		t.Fatal("truncated transfer accepted")
	}
	h, _, _ := checksum(filepath.Join(d, "checkpoint.tar"))
	if h != m.SHA256 {
		t.Fatal("existing valid archive overwritten")
	}
}
