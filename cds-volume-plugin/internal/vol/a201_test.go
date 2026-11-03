package vol_test

import . "cds-volume-plugin/internal/vol"

// A201 (OCR 2026-10-18, high) — a caller-supplied AAD is used for Seal but never
// written back to Manifest.AAD, so Attach (which opens with Manifest.AAD) hits a
// GCM authentication failure: every byte of that volume becomes unreadable.

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func a201Manifest(t *testing.T, root, vid string) Manifest {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(root, vid, "manifest.json"))
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	var m Manifest
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("decode manifest: %v", err)
	}
	return m
}

// TestA201_CustomAADVolumeIsAttachable: the volume must still open after a Put
// that supplied its own AAD — the manifest has to carry the AAD that actually
// sealed the data.
func TestA201_CustomAADVolumeIsAttachable(t *testing.T) {
	k, srv := go01MockKMS(t)
	root := t.TempDir()
	p := go01Plugin(t, root, k, srv)
	ctx := context.Background()
	const vid = "vol-1"
	data := []byte("rows,per,column,tenant\n")

	pd, err := p.CreateVolume(ctx, vid, "cds-data-"+vid)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if _, _, err := p.PutVolumeData(ctx, vid, "tenant-bound-aad", data); err != nil {
		t.Fatalf("put with custom AAD: %v", err)
	}

	if got := a201Manifest(t, root, vid).AAD; got != "tenant-bound-aad" {
		t.Fatalf("manifest AAD = %q, want the AAD that actually sealed the data: Attach opens with "+
			"Manifest.AAD and would hit a GCM auth failure", got)
	}

	base := t.TempDir()
	host, _, err := p.Attach(ctx, vid, base, "sandbox-1", pd, 0)
	if err != nil {
		t.Fatalf("Attach after a custom-AAD Put: %v", err)
	}
	got, err := os.ReadFile(host + "/data")
	if err != nil {
		t.Fatalf("read stage: %v", err)
	}
	if !bytes.Equal(got, data) {
		t.Fatalf("staged plaintext = %q, want %q", got, data)
	}
}

// TestA201_CustomAADSurvivesPluginRestart: Attach in a fresh process reads only
// what the manifest persisted, which is the case that turns the missing write-back
// into permanent data loss.
func TestA201_CustomAADSurvivesPluginRestart(t *testing.T) {
	k, srv := go01MockKMS(t)
	root := t.TempDir()
	p := go01Plugin(t, root, k, srv)
	ctx := context.Background()
	const vid = "vol-1"
	data := []byte("restart-boundary\n")

	pd, err := p.CreateVolume(ctx, vid, "cds-data-"+vid)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if _, _, err := p.PutVolumeData(ctx, vid, "tenant-bound-aad", data); err != nil {
		t.Fatalf("put: %v", err)
	}

	restarted := go01Plugin(t, root, k, srv)
	base := t.TempDir()
	host, _, err := restarted.Attach(ctx, vid, base, "sandbox-1", pd, 0)
	if err != nil {
		t.Fatalf("Attach through a restarted plugin: %v", err)
	}
	if got, _ := os.ReadFile(host + "/data"); !bytes.Equal(got, data) {
		t.Fatalf("staged plaintext = %q, want %q", got, data)
	}
}

// TestA201_IdempotentRetryWithoutAAD: a retry that omits the AAD must resolve it
// from the manifest (the sealing AAD), not from the freshly created default.
func TestA201_IdempotentRetryWithoutAAD(t *testing.T) {
	k, srv := go01MockKMS(t)
	root := t.TempDir()
	p := go01Plugin(t, root, k, srv)
	ctx := context.Background()
	const vid = "vol-1"
	data := []byte("idempotent\n")

	if _, err := p.CreateVolume(ctx, vid, "cds-data-"+vid); err != nil {
		t.Fatalf("create: %v", err)
	}
	if _, _, err := p.PutVolumeData(ctx, vid, "tenant-bound-aad", data); err != nil {
		t.Fatalf("put: %v", err)
	}
	n, idem, err := p.PutVolumeData(ctx, vid, "", data)
	if err != nil {
		t.Fatalf("retry without AAD: %v", err)
	}
	if !idem || n != uint64(len(data)) {
		t.Fatalf("retry = (n=%d, idempotent=%v), want (%d, true)", n, idem, len(data))
	}
}

// TestA201_RetryWithForeignAADIsRefusedClearly: an AAD that is not the sealing
// AAD cannot open the data. It must be named as an AAD mismatch, fail-closed,
// and must not rewrite the volume.
func TestA201_RetryWithForeignAADIsRefusedClearly(t *testing.T) {
	k, srv := go01MockKMS(t)
	root := t.TempDir()
	p := go01Plugin(t, root, k, srv)
	ctx := context.Background()
	const vid = "vol-1"
	data := []byte("idempotent\n")

	if _, err := p.CreateVolume(ctx, vid, "cds-data-"+vid); err != nil {
		t.Fatalf("create: %v", err)
	}
	if _, _, err := p.PutVolumeData(ctx, vid, "tenant-bound-aad", data); err != nil {
		t.Fatalf("put: %v", err)
	}
	_, _, err := p.PutVolumeData(ctx, vid, "another-aad", []byte("different-bytes"))
	if err == nil {
		t.Fatalf("a retry with a different AAD was accepted")
	}
	if got := a201Manifest(t, root, vid).AAD; got != "tenant-bound-aad" {
		t.Fatalf("refused retry rewrote manifest AAD to %q", got)
	}
}
