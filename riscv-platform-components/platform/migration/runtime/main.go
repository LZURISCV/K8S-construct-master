// rv-migrate-runtime owns no workload lifecycle outside containerd/CRI.
// Checkpoint exits the source task; restore is driven by the target kubelet.
package main

import (
	"archive/tar"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"

	meta "github.com/checkpoint-restore/checkpointctl/lib"
	cd "github.com/containerd/containerd"
	"github.com/containerd/containerd/api/types/runc/options"
	"github.com/containerd/containerd/content"
	"github.com/containerd/containerd/images"
	"github.com/containerd/containerd/namespaces"
	gogo "github.com/gogo/protobuf/proto"
	oci "github.com/opencontainers/image-spec/specs-go/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
	cri "k8s.io/cri-api/pkg/apis/runtime/v1"
)

const annotation = "migration.riscv.io/restore"
const format = "rv-migration-v1"

// containerd 2.1 CRI restore archive member; not defined by checkpointctl 1.1.
const statusDumpFile = "status.dump"

var safeID = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$`)
var root, upstream string

type manifest struct {
	Format      string            `json:"format"`
	ID          string            `json:"id"`
	Namespace   string            `json:"namespace"`
	PodUID      string            `json:"podUID"`
	Container   string            `json:"container"`
	ContainerID string            `json:"containerID"`
	SHA256      string            `json:"sha256"`
	Bytes       int64             `json:"bytes"`
	Started     string            `json:"started"`
	Finished    string            `json:"finished"`
	Environment map[string]string `json:"environment"`
}
type grant struct {
	ID        string `json:"id"`
	Namespace string `json:"namespace"`
	Pod       string `json:"pod"`
	PodUID    string `json:"podUID"`
	Container string `json:"container"`
	SHA256    string `json:"sha256"`
	Expires   int64  `json:"expires"`
}

func fail(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func emit(v any) { fail(json.NewEncoder(os.Stdout).Encode(v)) }
func dir(id string) (string, error) {
	if !safeID.MatchString(id) {
		return "", errors.New("invalid migration ID")
	}
	return filepath.Join(root, id), nil
}
func writeJSON(name string, v any) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(name), ".json-*")
	if e != nil {
		return e
	}
	temp := f.Name()
	defer os.Remove(temp)
	if e = f.Chmod(0600); e == nil {
		_, e = f.Write(b)
	}
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e != nil {
		return e
	}
	if ce != nil {
		return ce
	}
	return os.Rename(temp, name)
}
func readJSON(name string, v any) error {
	b, e := os.ReadFile(name)
	if e != nil {
		return e
	}
	return json.Unmarshal(b, v)
}
func checksum(name string) (string, int64, error) {
	f, e := os.Open(name)
	if e != nil {
		return "", 0, e
	}
	defer f.Close()
	h := sha256.New()
	n, e := io.Copy(h, f)
	return hex.EncodeToString(h.Sum(nil)), n, e
}
func command(name string, args ...string) (string, error) {
	b, e := exec.Command(name, args...).CombinedOutput()
	return strings.TrimSpace(string(b)), e
}
func environment() (map[string]string, error) {
	v := map[string]string{"arch": runtime.GOARCH, "os": runtime.GOOS}
	if runtime.GOOS != "linux" {
		return v, errors.New("node helper requires Linux")
	}
	for k, argv := range map[string][]string{"kernel": {"uname", "-r"}, "runc": {"runc", "--version"}, "criu": {"criu", "--version"}} {
		s, e := command(argv[0], argv[1:]...)
		if e != nil {
			return nil, fmt.Errorf("%s: %s: %w", k, s, e)
		}
		v[k] = s
	}
	if _, e := os.Stat("/sys/fs/cgroup/cgroup.controllers"); e == nil {
		v["cgroup"] = "v2"
	} else {
		v["cgroup"] = "v1"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	c, e := cd.New(upstream)
	if e != nil {
		return nil, e
	}
	defer c.Close()
	version, e := c.Version(ctx)
	if e != nil {
		return nil, e
	}
	v["containerd"] = version.Version
	var major, minor int
	fmt.Sscanf(strings.TrimPrefix(version.Version, "v"), "%d.%d", &major, &minor)
	if major != 2 || minor < 1 {
		return nil, fmt.Errorf("containerd 2.1+ in the 2.x series is required; found %s", version.Version)
	}
	if out, e := command("criu", "check"); e != nil {
		return nil, fmt.Errorf("criu check failed: %s", out)
	}
	return v, nil
}
func criConn() (*grpc.ClientConn, error) {
	return connect(upstream)
}
func connect(socket string) (*grpc.ClientConn, error) {
	return grpc.NewClient("passthrough:///rv-migration", grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", socket)
		}))
}
func proxyReady(socket string) error {
	cc, e := connect(socket)
	if e != nil {
		return e
	}
	defer cc.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	_, e = cri.NewRuntimeServiceClient(cc).Version(ctx, &cri.VersionRequest{Version: "0.1.0"})
	return e
}

// Native checkpoint with Exit=true fences the source before publishing an archive.
// The ordinary forensic CRI CheckpointContainer call keeps the source running.
func checkpoint(id, ns, uid, container, cid string) error {
	d, e := dir(id)
	if e != nil {
		return e
	}
	if e = os.MkdirAll(d, 0700); e != nil {
		return e
	}
	var old manifest
	if e = readJSON(filepath.Join(d, "manifest.json"), &old); e == nil {
		if old.Namespace != ns || old.PodUID != uid || old.ContainerID != cid {
			return errors.New("checkpoint ID belongs to another source")
		}
		h, n, e := checksum(filepath.Join(d, "checkpoint.tar"))
		if e != nil {
			return e
		}
		if h != old.SHA256 || n != old.Bytes {
			return errors.New("stored checkpoint is corrupt")
		}
		emit(old)
		return nil
	}
	if _, e = os.Stat(filepath.Join(d, "started.json")); e == nil {
		return errors.New("previous checkpoint did not finish; source may be stopped; inspect source and retained files before recovery")
	}
	env, e := environment()
	if e != nil {
		return e
	}
	ctx, cancel := context.WithTimeout(namespaces.WithNamespace(context.Background(), "k8s.io"), 20*time.Minute)
	defer cancel()
	cc, e := criConn()
	if e != nil {
		return e
	}
	defer cc.Close()
	rc := cri.NewRuntimeServiceClient(cc)
	s, e := rc.ContainerStatus(ctx, &cri.ContainerStatusRequest{ContainerId: cid})
	if e != nil {
		return e
	}
	st := s.GetStatus()
	if st == nil || st.State != cri.ContainerState_CONTAINER_RUNNING || st.Metadata.GetName() != container || st.Labels["io.kubernetes.pod.uid"] != uid || st.Labels["io.kubernetes.pod.namespace"] != ns {
		return errors.New("source identity or running state changed")
	}
	c, e := cd.New(upstream)
	if e != nil {
		return e
	}
	defer c.Close()
	ctr, e := c.LoadContainer(ctx, cid)
	if e != nil {
		return e
	}
	info, e := ctr.Info(ctx)
	if e != nil {
		return e
	}
	if info.Runtime.Name != "io.containerd.runc.v2" {
		return errors.New("only io.containerd.runc.v2 is supported")
	}
	specification, e := ctr.Spec(ctx)
	if e != nil {
		return e
	}
	allowedBinds := map[string]bool{"/etc/hosts": true, "/etc/hostname": true, "/etc/resolv.conf": true, "/dev/shm": true, "/dev/termination-log": true}
	for _, m := range specification.Mounts {
		if (m.Type == "bind" || hasOption(m.Options, "bind") || hasOption(m.Options, "rbind")) && !allowedBinds[m.Destination] {
			return fmt.Errorf("external/image volume mount is not supported: %s", m.Destination)
		}
	}
	task, e := ctr.Task(ctx, nil)
	if e != nil {
		return e
	}
	for _, name := range []string{"tcp", "tcp6"} {
		b, e := os.ReadFile(fmt.Sprintf("/proc/%d/net/%s", task.Pid(), name))
		if name == "tcp6" && os.IsNotExist(e) {
			continue
		}
		if e != nil {
			return e
		}
		for _, line := range strings.Split(string(b), "\n") {
			fields := strings.Fields(line)
			if len(fields) > 3 && fields[3] == "01" {
				return errors.New("v1 rejects established TCP connections; source was not checkpointed")
			}
		}
	}
	config := meta.ContainerConfig{ID: cid, Name: st.Metadata.Name, RootfsImageName: st.Image.Image, RootfsImageRef: st.ImageRef, RootfsImage: st.Image.Image, OCIRuntime: info.Runtime.Name, CheckpointedAt: time.Now(), CreatedTime: info.CreatedAt}
	if !strings.Contains(config.RootfsImageRef, "@sha256:") {
		return errors.New("runtime did not provide a name@digest base image reference")
	}
	config.RootfsImageName = config.RootfsImageRef
	m := manifest{Format: format, ID: id, Namespace: ns, PodUID: uid, Container: container, ContainerID: cid, Started: time.Now().UTC().Format(time.RFC3339Nano), Environment: env}
	if e = createJSON(filepath.Join(d, "started.json"), m); e != nil {
		return e
	}
	image, e := task.Checkpoint(ctx, func(i *cd.CheckpointTaskInfo) error {
		i.Name = "rv-checkpoint-" + id
		i.Options = &options.CheckpointOptions{Exit: true, WorkPath: d}
		return nil
	})
	if e != nil {
		return fmt.Errorf("checkpoint failed; retain %s for diagnosis: %w", d, e)
	}
	// Keep the checkpoint image/content on the source for recovery if export fails.
	b, e := content.ReadBlob(ctx, c.ContentStore(), image.Target())
	if e != nil {
		return e
	}
	var index oci.Index
	if e = json.Unmarshal(b, &index); e != nil {
		return e
	}
	f, e := os.OpenFile(filepath.Join(d, "checkpoint.partial"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	tw := tar.NewWriter(f)
	ok := false
	defer func() {
		tw.Close()
		f.Close()
		if !ok {
			fmt.Fprintln(os.Stderr, "source checkpoint content retained in containerd; export was incomplete")
		}
	}()
	for name, value := range map[string]any{meta.ConfigDumpFile: config, statusDumpFile: st} {
		b, e = json.Marshal(value)
		if e != nil {
			return e
		}
		if e = addBytes(tw, name, b); e != nil {
			return e
		}
	}
	found := map[string]bool{}
	for _, desc := range index.Manifests {
		switch desc.MediaType {
		case images.MediaTypeContainerd1Checkpoint:
			ra, e := c.ContentStore().ReaderAt(ctx, desc)
			if e != nil {
				return e
			}
			tr := tar.NewReader(content.NewReader(ra))
			for {
				hdr, e := tr.Next()
				if errors.Is(e, io.EOF) {
					break
				}
				if e != nil {
					ra.Close()
					return e
				}
				if !safeMember(hdr.Name) {
					ra.Close()
					return errors.New("unsafe CRIU checkpoint member")
				}
				hdr.Name = path.Join(meta.CheckpointDirectory, hdr.Name)
				if e = tw.WriteHeader(hdr); e != nil {
					ra.Close()
					return e
				}
				if _, e = io.Copy(tw, tr); e != nil {
					ra.Close()
					return e
				}
			}
			ra.Close()
			found["memory"] = true
		case oci.MediaTypeImageLayerGzip:
			ra, e := c.ContentStore().ReaderAt(ctx, desc)
			if e != nil {
				return e
			}
			e = addReader(tw, meta.RootFsDiffTar, desc.Size, content.NewReader(ra))
			ra.Close()
			if e != nil {
				return e
			}
			found["rootfs"] = true
		case images.MediaTypeContainerd1CheckpointConfig:
			b, e = content.ReadBlob(ctx, c.ContentStore(), desc)
			if e != nil {
				return e
			}
			var spec anypb.Any
			if e = proto.Unmarshal(b, &spec); e != nil {
				return e
			}
			if e = addBytes(tw, meta.SpecDumpFile, spec.Value); e != nil {
				return e
			}
			found["spec"] = true
		}
	}
	if !found["memory"] || !found["rootfs"] || !found["spec"] {
		return errors.New("checkpoint lacks memory, rootfs, or OCI specification")
	}
	if e = tw.Close(); e != nil {
		return e
	}
	if e = f.Sync(); e != nil {
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	if e = os.Rename(filepath.Join(d, "checkpoint.partial"), filepath.Join(d, "checkpoint.tar")); e != nil {
		return e
	}
	m.SHA256, m.Bytes, e = checksum(filepath.Join(d, "checkpoint.tar"))
	if e != nil {
		return e
	}
	m.Finished = time.Now().UTC().Format(time.RFC3339Nano)
	if e = writeJSON(filepath.Join(d, "manifest.json"), m); e != nil {
		return e
	}
	ok = true
	emit(m)
	return nil
}
func createJSON(name string, v any) error {
	f, e := os.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	defer f.Close()
	if e = json.NewEncoder(f).Encode(v); e != nil {
		return e
	}
	return f.Sync()
}
func hasOption(values []string, value string) bool {
	for _, v := range values {
		if v == value {
			return true
		}
	}
	return false
}
func safeMember(name string) bool {
	clean := path.Clean(name)
	return name != "" && !strings.HasPrefix(clean, "/") && clean != ".." && !strings.HasPrefix(clean, "../") && !strings.Contains(name, "\\")
}
func addReader(tw *tar.Writer, name string, size int64, r io.Reader) error {
	if e := tw.WriteHeader(&tar.Header{Name: name, Mode: 0600, Size: size, Typeflag: tar.TypeReg}); e != nil {
		return e
	}
	_, e := io.CopyN(tw, r, size)
	return e
}
func addBytes(tw *tar.Writer, name string, b []byte) error {
	return addReader(tw, name, int64(len(b)), strings.NewReader(string(b)))
}
func validateArchive(name, ns, uid, container string) error {
	f, e := os.Open(name)
	if e != nil {
		return e
	}
	defer f.Close()
	tr := tar.NewReader(f)
	found := map[string]bool{}
	for {
		h, e := tr.Next()
		if errors.Is(e, io.EOF) {
			break
		}
		if e != nil {
			return e
		}
		if !safeMember(h.Name) {
			return errors.New("unsafe archive path")
		}
		if h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeDir {
			return errors.New("unsupported archive member type")
		}
		n := path.Clean(h.Name)
		if found[n] {
			return errors.New("duplicate archive member")
		}
		found[n] = true
		if n == statusDumpFile {
			if h.Size > 4<<20 {
				return errors.New("oversized status metadata")
			}
			var s cri.ContainerStatus
			if e = json.NewDecoder(io.LimitReader(tr, 4<<20)).Decode(&s); e != nil {
				return e
			}
			if s.Labels["io.kubernetes.pod.namespace"] != ns || s.Labels["io.kubernetes.pod.uid"] != uid || s.Metadata.GetName() != container {
				return errors.New("archive source identity mismatch")
			}
		}
	}
	for _, n := range []string{statusDumpFile, meta.ConfigDumpFile, meta.SpecDumpFile, meta.RootFsDiffTar, path.Join(meta.CheckpointDirectory, "inventory.img")} {
		if !found[n] {
			return fmt.Errorf("archive missing %s", n)
		}
	}
	return nil
}
func importArchive(m manifest) error {
	if m.Format != format || m.Bytes <= 0 || m.Bytes > 1<<40 || len(m.SHA256) != 64 {
		return errors.New("invalid archive manifest")
	}
	d, e := dir(m.ID)
	if e != nil {
		return e
	}
	if e = os.MkdirAll(d, 0700); e != nil {
		return e
	}
	f, e := os.CreateTemp(d, ".import-*")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if e = f.Chmod(0600); e != nil {
		return e
	}
	h := sha256.New()
	n, e := io.Copy(io.MultiWriter(f, h), io.LimitReader(os.Stdin, m.Bytes+1))
	if e != nil {
		return e
	}
	if n != m.Bytes || hex.EncodeToString(h.Sum(nil)) != m.SHA256 {
		return errors.New("archive transfer checksum/size mismatch")
	}
	if e = f.Sync(); e != nil {
		return e
	}
	f.Close()
	if e = validateArchive(f.Name(), m.Namespace, m.PodUID, m.Container); e != nil {
		return e
	}
	if e = os.Rename(f.Name(), filepath.Join(d, "checkpoint.tar")); e != nil {
		return e
	}
	if e = writeJSON(filepath.Join(d, "manifest.json"), m); e != nil {
		return e
	}
	emit(m)
	return nil
}
func authorize(g grant) error {
	d, e := dir(g.ID)
	if e != nil {
		return e
	}
	var m manifest
	if e = readJSON(filepath.Join(d, "manifest.json"), &m); e != nil {
		return e
	}
	if g.Namespace != m.Namespace || g.Container != m.Container || g.SHA256 != m.SHA256 || g.Pod == "" || g.PodUID == "" || g.Expires <= time.Now().Unix() {
		return errors.New("invalid restore authorization")
	}
	h, n, e := checksum(filepath.Join(d, "checkpoint.tar"))
	if e != nil {
		return e
	}
	if h != m.SHA256 || n != m.Bytes {
		return errors.New("checkpoint changed")
	}
	if e = writeJSON(filepath.Join(d, "grant.json"), g); e != nil {
		return e
	}
	emit(map[string]bool{"authorized": true})
	return nil
}

// Raw protobuf forwarding keeps unmodified CRI methods (including image service)
// compatible with the upstream runtime. Only CreateContainer is rewritten.
type frame []byte
type codec struct{}

func (codec) Name() string { return "proto" }
func (codec) Marshal(v any) ([]byte, error) {
	switch x := v.(type) {
	case *frame:
		return []byte(*x), nil
	case gogo.Message:
		return gogo.Marshal(x)
	default:
		return nil, fmt.Errorf("unsupported protobuf %T", v)
	}
}
func (codec) Unmarshal(b []byte, v any) error {
	switch x := v.(type) {
	case *frame:
		*x = append((*x)[:0], b...)
		return nil
	case gogo.Message:
		return gogo.Unmarshal(b, x)
	default:
		return fmt.Errorf("unsupported protobuf %T", v)
	}
}
func rewrite(req *cri.CreateContainerRequest) error {
	id := req.GetSandboxConfig().GetAnnotations()[annotation]
	if id == "" {
		return nil
	}
	d, e := dir(id)
	if e != nil {
		return e
	}
	var g grant
	if e = readJSON(filepath.Join(d, "grant.json"), &g); e != nil {
		return e
	}
	s := req.GetSandboxConfig().GetMetadata()
	c := req.GetConfig().GetMetadata()
	if s == nil || c == nil || g.ID != id || g.Namespace != s.Namespace || g.Pod != s.Name || g.PodUID != s.Uid || g.Container != c.Name || c.Attempt != 0 || g.Expires < time.Now().Unix() {
		return errors.New("restore authorization does not match target Pod UID/container/attempt")
	}
	var m manifest
	if e = readJSON(filepath.Join(d, "manifest.json"), &m); e != nil {
		return e
	}
	h, n, e := checksum(filepath.Join(d, "checkpoint.tar"))
	if e != nil {
		return e
	}
	if h != g.SHA256 || h != m.SHA256 || n != m.Bytes {
		return errors.New("restore archive failed integrity check")
	}
	req.Config.Image = &cri.ImageSpec{Image: filepath.Join(d, "checkpoint.tar"), UserSpecifiedImage: req.Config.GetImage().GetImage()}
	return nil
}
func serve(socket string) error {
	if e := os.MkdirAll(filepath.Dir(socket), 0700); e != nil {
		return e
	}
	// Never unlink a live socket (a second instance must not steal the endpoint).
	if _, e := os.Lstat(socket); e == nil {
		conn, err := net.DialTimeout("unix", socket, time.Second)
		if err == nil {
			conn.Close()
			return errors.New("proxy socket is already serving")
		}
		if e = os.Remove(socket); e != nil {
			return e
		}
	}
	l, e := net.Listen("unix", socket)
	if e != nil {
		return e
	}
	defer l.Close()
	if e = os.Chmod(socket, 0600); e != nil {
		return e
	}
	cc, e := criConn()
	if e != nil {
		return e
	}
	defer cc.Close()
	s := grpc.NewServer(grpc.ForceServerCodec(codec{}), grpc.MaxRecvMsgSize(16<<20), grpc.UnknownServiceHandler(func(_ any, ss grpc.ServerStream) error {
		method, ok := grpc.MethodFromServerStream(ss)
		if !ok {
			return errors.New("missing CRI method")
		}
		var b frame
		if e := ss.RecvMsg(&b); e != nil {
			return e
		}
		if method == "/runtime.v1.RuntimeService/CreateContainer" {
			var req cri.CreateContainerRequest
			if e := gogo.Unmarshal(b, &req); e != nil {
				return e
			}
			if e := rewrite(&req); e != nil {
				return e
			}
			raw, e := gogo.Marshal(&req)
			if e != nil {
				return e
			}
			b = raw
		}
		cs, e := cc.NewStream(ss.Context(), &grpc.StreamDesc{ServerStreams: true, ClientStreams: true}, method, grpc.ForceCodec(codec{}))
		if e != nil {
			return e
		}
		if e = cs.SendMsg(&b); e != nil {
			return e
		}
		cs.CloseSend()
		header, e := cs.Header()
		if e != nil {
			return e
		}
		if e = ss.SendHeader(header); e != nil {
			return e
		}
		for {
			var out frame
			e = cs.RecvMsg(&out)
			if errors.Is(e, io.EOF) {
				ss.SetTrailer(cs.Trailer())
				return nil
			}
			if e != nil {
				ss.SetTrailer(cs.Trailer())
				return e
			}
			if e = ss.SendMsg(&out); e != nil {
				return e
			}
		}
	}))
	return s.Serve(l)
}
func main() {
	flag.StringVar(&root, "root", "/var/lib/rv-migration", "checkpoint directory")
	flag.StringVar(&upstream, "upstream", "/run/containerd/containerd.sock", "upstream containerd socket")
	socket := flag.String("socket", "/run/rv-migration/cri.sock", "proxy socket")
	flag.Parse()
	args := flag.Args()
	if len(args) == 0 {
		fail(errors.New("commands: probe, proxy, checkpoint, export, import, grant"))
	}
	rootAbs, e := filepath.Abs(root)
	fail(e)
	root = rootAbs
	switch args[0] {
	case "probe":
		v, e := environment()
		fail(e)
		emit(v)
	case "proxy-ready":
		fail(proxyReady(*socket))
		emit(map[string]bool{"ready": true})
	case "preflight":
		v, e := environment()
		fail(e)
		fail(proxyReady(*socket))
		b, e := os.ReadFile("/etc/kubernetes/kubelet.conf")
		fail(e)
		if !strings.Contains(string(b), "--container-runtime-endpoint=unix://"+*socket) {
			fail(errors.New("kubelet is not configured to use migration CRI proxy"))
		}
		emit(v)
	case "proxy":
		fail(serve(*socket))
	case "checkpoint":
		if len(args) != 6 {
			fail(errors.New("checkpoint ID NAMESPACE POD_UID CONTAINER CONTAINER_ID"))
		}
		fail(checkpoint(args[1], args[2], args[3], args[4], args[5]))
	case "export":
		if len(args) != 2 {
			fail(errors.New("export ID"))
		}
		d, e := dir(args[1])
		fail(e)
		f, e := os.Open(filepath.Join(d, "checkpoint.tar"))
		fail(e)
		defer f.Close()
		_, e = io.Copy(os.Stdout, f)
		fail(e)
	case "import":
		if len(args) != 2 {
			fail(errors.New("import MANIFEST_JSON"))
		}
		var m manifest
		fail(json.Unmarshal([]byte(args[1]), &m))
		fail(importArchive(m))
	case "grant":
		var g grant
		fail(json.NewDecoder(os.Stdin).Decode(&g))
		fail(authorize(g))
	case "verify":
		if len(args) != 3 {
			fail(errors.New("verify CONTAINER_ID POD_UID"))
		}
		cc, e := criConn()
		fail(e)
		defer cc.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		s, e := cri.NewRuntimeServiceClient(cc).ContainerStatus(ctx, &cri.ContainerStatusRequest{ContainerId: args[1]})
		fail(e)
		if s.GetStatus().GetAnnotations()["restored"] != "true" || s.GetStatus().GetLabels()["io.kubernetes.pod.uid"] != args[2] || s.GetStatus().GetState() != cri.ContainerState_CONTAINER_RUNNING {
			fail(errors.New("target is not a running CRI-restored container with the expected Pod UID"))
		}
		emit(map[string]bool{"restored": true})
	case "describe-source":
		if len(args) != 3 {
			fail(errors.New("describe-source CONTAINER_ID POD_UID"))
		}
		cc, e := criConn()
		fail(e)
		defer cc.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		s, e := cri.NewRuntimeServiceClient(cc).ContainerStatus(ctx, &cri.ContainerStatusRequest{ContainerId: args[1]})
		fail(e)
		if s.GetStatus().GetLabels()["io.kubernetes.pod.uid"] != args[2] || s.GetStatus().GetState() != cri.ContainerState_CONTAINER_RUNNING || !strings.Contains(s.GetStatus().GetImageRef(), "@sha256:") {
			fail(errors.New("source identity/state/image reference is unsuitable"))
		}
		emit(map[string]string{"imageRef": s.GetStatus().GetImageRef()})
	case "prepare-image":
		if len(args) != 2 || !strings.Contains(args[1], "@sha256:") {
			fail(errors.New("prepare-image requires name@sha256:digest"))
		}
		ctx, cancel := context.WithTimeout(namespaces.WithNamespace(context.Background(), "k8s.io"), 15*time.Minute)
		defer cancel()
		c, e := cd.New(upstream)
		fail(e)
		defer c.Close()
		_, e = c.Pull(ctx, args[1])
		fail(e)
		emit(map[string]bool{"prepared": true})
	default:
		fail(errors.New("unknown command"))
	}
}
