package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVerifiedLocalArchiveRetainsArchiveAndChecksIdentity(t *testing.T) {
	m := archiveFixture(t, "local-recovery")
	got, e := verifiedArchive(m.ID, m.SHA256)
	if e != nil || got.SHA256 != m.SHA256 {
		t.Fatalf("valid archive rejected: %v", e)
	}
	if _, e = verifiedArchive(m.ID, strings.Repeat("0", 64)); e == nil {
		t.Fatal("wrong checksum accepted")
	}
	d, _ := dir(m.ID)
	if e = os.WriteFile(filepath.Join(d, "checkpoint.tar"), []byte("changed"), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = verifiedArchive(m.ID, m.SHA256); e == nil {
		t.Fatal("changed local archive accepted")
	}
}

func TestPinnedImageDigestMustMatchCachedTarget(t *testing.T) {
	digest := "sha256:" + strings.Repeat("a", 64)
	if e := checkPinnedTarget("localhost/counter@"+digest, digest); e != nil {
		t.Fatal(e)
	}
	for _, ref := range []string{"localhost/counter:v1", "localhost/counter@sha256:" + strings.Repeat("b", 64)} {
		if e := checkPinnedTarget(ref, digest); e == nil {
			t.Fatal("unpinned or mismatched image accepted")
		}
	}
}
