package main

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	cd "github.com/containerd/containerd"
	"github.com/containerd/containerd/errdefs"
	"github.com/containerd/containerd/images"
	"github.com/containerd/platforms"
	"github.com/distribution/reference"
	cri "k8s.io/cri-api/pkg/apis/runtime/v1"
)

func completeLocalImage(ctx context.Context, c *cd.Client, ref string) (cd.Image, error) {
	image, e := c.GetImage(ctx, ref)
	if e != nil {
		return nil, e
	}
	if e = checkPinnedTarget(ref, image.Target().Digest.String()); e != nil {
		return nil, e
	}
	available, _, _, missing, e := images.Check(ctx, c.ContentStore(), image.Target(), platforms.Default())
	if e != nil {
		return nil, e
	}
	if !available || len(missing) != 0 {
		return nil, fmt.Errorf("local base image is incomplete: %w", errdefs.ErrNotFound)
	}
	return image, nil
}

func checkPinnedTarget(ref, actual string) error {
	parsed, e := reference.ParseAnyReference(ref)
	if e != nil {
		return e
	}
	pinned, ok := parsed.(reference.Digested)
	if !ok || pinned.Digest().String() != actual {
		return errors.New("base image reference must pin its exact manifest/index digest")
	}
	return nil
}

func prepareBaseImage(ctx context.Context, c *cd.Client, ref string, localOnly bool) error {
	_, e := completeLocalImage(ctx, c, ref)
	if e == nil {
		return nil
	}
	if localOnly || !errdefs.IsNotFound(e) {
		return fmt.Errorf("base image cache check failed (no network requested): %w", e)
	}
	if _, e = c.Pull(ctx, ref); e != nil {
		return e
	}
	_, e = completeLocalImage(ctx, c, ref)
	return e
}

// Offline OCI imports often have no RepoDigests. Resolve the running image's
// config ID, then retain a name@manifest-digest alias without changing any tag.
func pinSourceImage(ctx context.Context, c *cd.Client, status *cri.ContainerStatus) (string, error) {
	var image cd.Image
	var e error
	ref := status.GetImageRef()
	if strings.Contains(ref, "@sha256:") {
		image, e = completeLocalImage(ctx, c, ref)
		if e != nil {
			return "", e
		}
		return ref, nil
	}
	image, e = c.GetImage(ctx, status.GetImage().GetImage())
	if e != nil {
		return "", e
	}
	config, e := image.Config(ctx)
	if e != nil {
		return "", e
	}
	if config.Digest.String() != ref {
		return "", errors.New("source tag no longer identifies the running image config; refusing to checkpoint")
	}
	named, e := reference.ParseNormalizedNamed(image.Name())
	if e != nil {
		return "", fmt.Errorf("source needs a named image tag: %w", e)
	}
	pinned, e := reference.WithDigest(reference.TrimNamed(named), image.Target().Digest)
	if e != nil {
		return "", e
	}
	alias := image.Metadata()
	alias.Name = pinned.String()
	alias.Labels = map[string]string{}
	for k, v := range image.Labels() {
		alias.Labels[k] = v
	}
	alias.Labels["io.cri-containerd.image"] = "managed"
	if _, e = c.ImageService().Create(ctx, alias); e != nil && !errdefs.IsAlreadyExists(e) {
		return "", e
	}
	_, e = completeLocalImage(ctx, c, alias.Name)
	return alias.Name, e
}

func verifiedArchive(id, expected string) (manifest, error) {
	var m manifest
	d, e := dir(id)
	if e != nil {
		return m, e
	}
	if e = readJSON(filepath.Join(d, "manifest.json"), &m); e != nil {
		return m, e
	}
	if m.ID != id || m.Format != format || m.SHA256 != expected {
		return m, errors.New("local archive manifest does not match the transaction")
	}
	h, n, e := checksum(filepath.Join(d, "checkpoint.tar"))
	if e != nil {
		return m, e
	}
	if h != expected || n != m.Bytes {
		return m, errors.New("local archive checksum/size mismatch")
	}
	e = validateArchive(filepath.Join(d, "checkpoint.tar"), m.Namespace, m.PodUID, m.Container)
	return m, e
}
