package server

import (
	"errors"
	"testing"

	"github.com/containerd/containerd/v2/client"
	"github.com/containerd/containerd/v2/core/images"
	"github.com/containerd/errdefs"
)

func TestRVCompleteLocalRestoreDoesNotPull(t *testing.T) {
	image := client.NewImage(&client.Client{}, images.Image{Name: "example.test/counter@sha256:abc"})
	pulled := false
	got, err := rvGetOrPullImage(func() (client.Image, error) { return image, nil },
		func(client.Image) error { return nil },
		func() (client.Image, error) { pulled = true; return nil, errors.New("network must not be used") })
	if err != nil || got != image || pulled {
		t.Fatalf("cached restore accessed registry: image=%v error=%v pulled=%v", got, err, pulled)
	}
}

func TestRVMissingOrIncompleteRestoreImageFallsBackToPull(t *testing.T) {
	image := client.NewImage(&client.Client{}, images.Image{Name: "cached"})
	for _, missingMetadata := range []bool{true, false} {
		pulls := 0
		got, err := rvGetOrPullImage(func() (client.Image, error) {
			if missingMetadata {
				return nil, errdefs.ErrNotFound
			}
			return image, nil
		}, func(client.Image) error { return errdefs.ErrNotFound },
			func() (client.Image, error) { pulls++; return image, nil })
		if err != nil || got != image || pulls != 1 {
			t.Fatalf("cross-node fallback failed: %v pulls=%d", err, pulls)
		}
	}
}

func TestRVRestoreCacheErrorsAreNotHiddenByPull(t *testing.T) {
	for _, fromCheck := range []bool{true, false} {
		problem := errors.New("cache read or digest error")
		_, err := rvGetOrPullImage(func() (client.Image, error) {
			if fromCheck {
				return client.NewImage(&client.Client{}, images.Image{}), nil
			}
			return nil, problem
		}, func(client.Image) error { return problem },
			func() (client.Image, error) {
				t.Fatal("must not mask a cache integrity error by pulling")
				return nil, nil
			})
		if !errors.Is(err, problem) {
			t.Fatalf("original error lost: %v", err)
		}
	}
}
