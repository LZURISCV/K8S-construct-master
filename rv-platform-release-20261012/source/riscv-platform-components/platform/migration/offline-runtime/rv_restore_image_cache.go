package server

import (
	"context"
	"fmt"

	"github.com/containerd/containerd/v2/client"
	"github.com/containerd/containerd/v2/core/images"
	"github.com/containerd/errdefs"
	"github.com/containerd/platforms"
	"github.com/distribution/reference"
)

// Prefer an exact, complete local image. Registry access remains available for
// ordinary cross-node restores when the image is missing locally.
func rvRestoreBaseImage(ctx context.Context, c *client.Client, ref string) (client.Image, error) {
	return rvGetOrPullImage(
		func() (client.Image, error) { return c.GetImage(ctx, ref) },
		func(image client.Image) error {
			parsed, err := reference.ParseAnyReference(ref)
			if err != nil {
				return err
			}
			pinned, ok := parsed.(reference.Digested)
			if !ok || pinned.Digest() != image.Target().Digest {
				return fmt.Errorf("checkpoint base image cache digest mismatch")
			}
			available, _, _, missing, err := images.Check(ctx, c.ContentStore(), image.Target(), platforms.Default())
			if err != nil {
				return err
			}
			if !available || len(missing) != 0 {
				return fmt.Errorf("incomplete checkpoint base image: %w", errdefs.ErrNotFound)
			}
			return nil
		},
		func() (client.Image, error) { return c.Pull(ctx, ref) },
	)
}

func rvGetOrPullImage(get func() (client.Image, error), check func(client.Image) error, pull func() (client.Image, error)) (client.Image, error) {
	image, err := get()
	if err == nil {
		err = check(image)
		if err == nil {
			return image, nil
		}
	}
	if !errdefs.IsNotFound(err) {
		return nil, err
	}
	return pull()
}
