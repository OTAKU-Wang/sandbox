package main

// A199 (OCR 2026-10-18, high) — volume_id is not validated at the RPC boundary
// and paths() is the only aggregation point for VolumeRoot containment, so a
// crafted id reaches outside the volume root on the entries that skip the check.

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var a199Hostile = []string{
	"../../evil",
	"../evil",
	"vol/../../evil",
	"/etc/passwd",
	"/tmp/evil",
	"..",
	"....//evil",
	"vol\\..\\..\\evil",
	"a/b",
	"a\\b",
	"",
	"vol\u0000name",
	"vol\nname",
	"vol name",
	strings.Repeat("v", 201),
}

// a199Root gives VolumeRoot two ancestors inside the temp dir, so "../../evil"
// resolves to a directory that still belongs to this test (and to nothing outside it).
func a199Root(t *testing.T) (root, foreign string) {
	t.Helper()
	base := t.TempDir()
	root = filepath.Join(base, "srv", "lib", "volumes")
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatalf("volume root: %v", err)
	}
	foreign = filepath.Join(base, "srv", "evil")
	if got := filepath.Clean(filepath.Join(root, "../../evil")); got != foreign {
		t.Fatalf("harness assumption broken: traversal lands at %s, not %s", got, foreign)
	}
	return root, foreign
}

// a199Plant writes a complete, legitimate-looking volume OUTSIDE VolumeRoot, so
// an escape has a real target to hit rather than a hypothetical one.
func a199Plant(t *testing.T, dir, relID string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatalf("plant dir: %v", err)
	}
	m := Manifest{VolumeID: relID, Name: "cds-data-" + relID, Cipher: "aes-128-gcm(fallback,non-GM)",
		DekRef: "vkey-0f1e2d3c4b5a69788796a5b4c3d2e1f0", AAD: "planted"}
	b, err := json.Marshal(&m)
	if err != nil {
		t.Fatalf("encode planted manifest: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), b, 0600); err != nil {
		t.Fatalf("plant manifest: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "data.ct"), []byte("ciphertext of someone else's volume"), 0600); err != nil {
		t.Fatalf("plant ct: %v", err)
	}
}

func a199Entries(t *testing.T, p *Plugin, id string) map[string]error {
	t.Helper()
	ctx := context.Background()
	base := t.TempDir()
	return map[string]error{
		"CreateVolume":  func() error { _, err := p.CreateVolume(ctx, id, "cds-data-x"); return err }(),
		"PutVolumeData": func() error { _, _, err := p.PutVolumeData(ctx, id, "", []byte("x")); return err }(),
		"Attach": func() error {
			_, _, err := p.Attach(ctx, id, base, "sandbox-1", go01PrivateData("vkey-0f1e2d3c4b5a69788796a5b4c3d2e1f0"), 1)
			return err
		}(),
		"Detach":        func() error { return p.Detach(ctx, id, base, 0) }(),
		"Destroy":       func() error { return p.Destroy(ctx, id) }(),
		"GetVolumeInfo": func() error { _, err := p.GetVolumeInfo(id); return err }(),
	}
}

func TestA199_EveryEntryRejectsHostileVolumeID(t *testing.T) {
	k, srv := go01MockKMS(t)
	root, _ := a199Root(t)
	p := go01Plugin(t, root, k, srv)

	for _, id := range a199Hostile {
		for entry, err := range a199Entries(t, p, id) {
			if err == nil {
				t.Fatalf("%s accepted volume_id %q: the id reaches outside VolumeRoot", entry, id)
			}
			if !strings.Contains(err.Error(), "volume_id") {
				t.Errorf("%s: rejection must name volume_id, got: %v", entry, err)
			}
		}
	}
}

func TestA199_GetVolumeInfoCannotReadOutsideVolumeRoot(t *testing.T) {
	k, srv := go01MockKMS(t)
	root, foreign := a199Root(t)
	p := go01Plugin(t, root, k, srv)
	const rel = "../../evil"
	a199Plant(t, foreign, rel)

	m, err := p.GetVolumeInfo(rel)
	if err == nil {
		t.Fatalf("GetVolumeInfo(%q) returned the foreign manifest (volume_id=%s, dek_ref=%s) from %s",
			rel, m.VolumeID, m.DekRef, foreign)
	}
	if _, statErr := os.Stat(foreign); statErr != nil {
		t.Fatalf("the planted tree vanished: %v", statErr)
	}
}

func TestA199_DestroyCannotDeleteOutsideVolumeRoot(t *testing.T) {
	k, srv := go01MockKMS(t)
	root, foreign := a199Root(t)
	p := go01Plugin(t, root, k, srv)
	const rel = "../../evil"
	a199Plant(t, foreign, rel)

	derr := p.Destroy(context.Background(), rel)
	_, statErr := os.Stat(foreign)
	if statErr != nil {
		t.Fatalf("Destroy(%q) destroyed a tree outside VolumeRoot (%s): %v", rel, foreign, statErr)
	}
	if derr == nil {
		t.Fatalf("Destroy(%q) reported success for a tree outside VolumeRoot", rel)
	}
	if n := k.revokedCount(); n != 0 {
		t.Fatalf("Destroy(%q) revoked %d key(s) outside VolumeRoot", rel, n)
	}
}

func TestA199_PutVolumeDataCannotWriteOutsideVolumeRoot(t *testing.T) {
	k, srv := go01MockKMS(t)
	root, foreign := a199Root(t)
	p := go01Plugin(t, root, k, srv)
	ctx := context.Background()
	const rel = "../../evil"
	a199Plant(t, foreign, rel)
	before := a199Tree(t, foreign)

	if _, _, err := p.PutVolumeData(ctx, rel, "", []byte("written outside the root")); err == nil {
		t.Fatalf("PutVolumeData(%q) was accepted", rel)
	}
	if after := a199Tree(t, foreign); strings.Join(after, ",") != strings.Join(before, ",") {
		t.Fatalf("PutVolumeData(%q) touched the foreign tree: before=%v after=%v", rel, before, after)
	}
}

func TestA199_RejectionDoesNotEchoRawHostileID(t *testing.T) {
	k, srv := go01MockKMS(t)
	root, _ := a199Root(t)
	p := go01Plugin(t, root, k, srv)
	_, err := p.GetVolumeInfo("vol\n2026-10-19 INFO forged log line")
	if err == nil {
		t.Fatalf("newline-bearing volume_id accepted")
	}
	if strings.Contains(err.Error(), "\n") {
		t.Errorf("rejection let the id forge a second log line: %q", err.Error())
	}
}

func TestA199_LegitimateVolumeIDStillWorks(t *testing.T) {
	k, srv := go01MockKMS(t)
	root, _ := a199Root(t)
	p := go01Plugin(t, root, k, srv)
	ctx := context.Background()
	// The shape the control plane actually sends: cds-data-<session_id>
	// (app/services/cube_runtime_adapter.py:277,532,734,996).
	const id = "cds-data-0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
	data := []byte("legitimate payload\n")

	pd, err := p.CreateVolume(ctx, id, "cds-data-x")
	if err != nil {
		t.Fatalf("CreateVolume(%q): %v", id, err)
	}
	if _, _, err := p.PutVolumeData(ctx, id, "", data); err != nil {
		t.Fatalf("PutVolumeData(%q): %v", id, err)
	}
	if m, err := p.GetVolumeInfo(id); err != nil || m.VolumeID != id {
		t.Fatalf("GetVolumeInfo(%q) = (%v, %v)", id, m, err)
	}
	base := t.TempDir()
	host, _, err := p.Attach(ctx, id, base, "sandbox-1", pd, 1)
	if err != nil {
		t.Fatalf("Attach(%q): %v", id, err)
	}
	if _, statErr := os.Stat(host); statErr != nil {
		t.Fatalf("staged plaintext missing at %s: %v", host, statErr)
	}
	if err := p.Detach(ctx, id, base, 0); err != nil {
		t.Fatalf("Detach(%q): %v", id, err)
	}
	if err := p.Destroy(ctx, id); err != nil {
		t.Fatalf("Destroy(%q): %v", id, err)
	}
}

func a199Tree(t *testing.T, dir string) []string {
	t.Helper()
	var out []string
	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, rerr := filepath.Rel(dir, path)
		if rerr != nil {
			return rerr
		}
		out = append(out, rel)
		return nil
	})
	if err != nil {
		t.Fatalf("walk %s: %v", dir, err)
	}
	return out
}
