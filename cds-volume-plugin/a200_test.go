package main

// A200 (OCR 2026-10-18, high) — the destroy-semantics cluster. Iron law:
// "销毁即不可恢复" and "shred 失败要报错". Ledger facts: Destroy never touches
// the staged plaintext, discards every shred error, and shredFile unlinks even
// when the zero-fill failed.

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// a200FuncBody returns the source of a top-level func in this package, so the
// syscall ORDER of a non-observable durability step can be asserted.
func a200FuncBody(t *testing.T, name string) string {
	t.Helper()
	marker := "func " + name + "("
	for _, file := range []string{"plugin.go", "layout.go", "cipher.go", "rootfs.go", "server.go"} {
		b, err := os.ReadFile(file)
		if err != nil {
			t.Fatalf("read package source: %v", err)
		}
		i := strings.Index(string(b), marker)
		if i < 0 {
			continue
		}
		rest := string(b)[i:]
		if j := strings.Index(rest[len(marker):], "\nfunc "); j >= 0 {
			return rest[:len(marker)+j]
		}
		return rest
	}
	t.Fatalf("func %s not found in package source", name)
	return ""
}

// a200Attach creates, fills and attaches a volume, returning the staged
// plaintext path (the 0400 cleartext Cubelet mounts into the MicroVM).
func a200Attach(t *testing.T, p *Plugin, k *go01KMS, base, vid string) string {
	t.Helper()
	ctx := context.Background()
	pd, err := p.CreateVolume(ctx, vid, "cds-data-"+vid)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if pd != go01PrivateData(k.Refs[0]) {
		t.Fatalf("private_data mismatch: %s", pd)
	}
	if _, _, err := p.PutVolumeData(ctx, vid, "", []byte("secret-data")); err != nil {
		t.Fatalf("put: %v", err)
	}
	host, _, err := p.Attach(ctx, vid, base, "sandbox-1", pd, 1)
	if err != nil {
		t.Fatalf("attach: %v", err)
	}
	if _, err := os.Stat(host); err != nil {
		t.Fatalf("stage plaintext must exist after Attach: %v", err)
	}
	return host
}

// TestA200_DestroyShredsStagedPlaintext: Destroy is the terminal crypto-erase,
// and the staged plaintext under <base>/cds-<vid> is the one copy that outlives
// the key revoke in cleartext, so Destroy must wipe it itself.
func TestA200_DestroyShredsStagedPlaintext(t *testing.T) {
	k, srv := go01MockKMS(t)
	p := go01Plugin(t, t.TempDir(), k, srv)
	base := t.TempDir()
	const vid = "vol-1"

	host := a200Attach(t, p, k, base, vid)

	if err := p.Destroy(context.Background(), vid); err != nil {
		t.Fatalf("Destroy: %v", err)
	}
	if _, err := os.Stat(host); !os.IsNotExist(err) {
		t.Fatalf("stage plaintext SURVIVED Destroy at %s (stat err=%v): the DEK is revoked but "+
			"the cleartext is still on disk", host, err)
	}
}

// TestA200_ShredFailureKeepsFile: a shred that failed its zero-fill must not
// unlink, otherwise the caller is told the data is gone when it is not.
// /dev/full fails every write with ENOSPC without root or a fault injector.
func TestA200_ShredFailureKeepsFile(t *testing.T) {
	if _, err := os.Stat("/dev/full"); err != nil {
		t.Skip("/dev/full unavailable on this host")
	}
	link := filepath.Join(t.TempDir(), "data.ct")
	if err := os.Symlink("/dev/full", link); err != nil {
		t.Skipf("cannot symlink /dev/full: %v", err)
	}

	err := shredFile(link, 4096)
	if err == nil {
		t.Fatalf("shred through a writer that fails every write reported success")
	}
	if _, statErr := os.Lstat(link); statErr != nil {
		t.Fatalf("shredFile unlinked %q after the zero-fill FAILED (%v): a failed shred must RETAIN "+
			"the file so the operator can retry, got lstat err=%v", link, err, statErr)
	}
}

// TestA200_ShredFsyncsBeforeUnlink: without a sync between the zero-fill and
// the unlink, "shredded" only means the page cache agreed. Durability of the
// zeroing is hardware-gated; the syscall order in front of it is not.
func TestA200_ShredFsyncsBeforeUnlink(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "data.ct")
	if err := os.WriteFile(path, []byte("plain"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := shredFile(path, 5); err != nil {
		t.Fatalf("shredFile: %v", err)
	}

	body := a200FuncBody(t, "shredFile")
	syncAt := strings.Index(body, ".Sync()")
	removeAt := strings.Index(body, "os.Remove(path)")
	if syncAt < 0 {
		t.Fatalf("shredFile never fsyncs the zeroed file; body:\n%s", body)
	}
	if removeAt >= 0 && syncAt > removeAt {
		t.Fatalf("shredFile unlinks before it fsyncs; body:\n%s", body)
	}
}

// TestA200_DestroyReportsShredFailure: Destroy discards every shred error, so a
// volume whose files could not be wiped is still reported as destroyed.
func TestA200_DestroyReportsShredFailure(t *testing.T) {
	root := t.TempDir()
	k, srv := go01MockKMS(t)
	p := go01Plugin(t, root, k, srv)
	const vid = "vol-1"
	ctx := context.Background()

	if _, err := p.CreateVolume(ctx, vid, "cds-data-1"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := p.PutVolumeData(ctx, vid, "", []byte("secret-data")); err != nil {
		t.Fatal(err)
	}
	ct := filepath.Join(root, vid, "data.ct")
	if err := os.Remove(ct); err != nil {
		t.Fatal(err)
	}
	// Hostile state: this shred target can never be opened for writing.
	if err := os.Mkdir(ct, 0700); err != nil {
		t.Fatal(err)
	}

	err := p.Destroy(ctx, vid)
	if err == nil {
		t.Fatalf("Destroy reported success although a shred failed (iron law: shred 失败要报错)")
	}
	if !strings.Contains(err.Error(), "shred") {
		t.Fatalf("Destroy must name the failed shred, got: %v", err)
	}
	if _, statErr := os.Lstat(filepath.Join(root, vid, "manifest.json")); statErr != nil {
		t.Fatalf("Destroy removed the manifest although the ciphertext shred failed; the tree must "+
			"be RETAINED for retry (lstat: %v)", statErr)
	}
}
