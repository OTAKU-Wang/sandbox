package vol_test

import . "cds-volume-plugin/internal/vol"

// A199 (OCR 2026-10-18, high) — layout half. volume_id arrives over the socket
// from the control plane and layout.go's paths() is the single aggregation point
// where it becomes a host directory. The RPC-boundary whitelist lives in
// plugin.go; these tests pin the LAST line of defence in layout.go itself: an id
// that resolves outside VolumeRoot must be refused by the layout layer even when
// it is called directly, and must never yield a usable path.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Escaping ids only: ids that are merely malformed (spaces, glob chars) are the
// entry-layer whitelist's business, not the layout layer's containment check.
var a199LayoutEscapes = []string{
	"../../evil",
	"../evil",
	"vol/../../evil",
	"..",
	"../..",
	"a/../..",
	"./../evil",
}

// a199LayoutTree: base/srv/lib/volumes is VolumeRoot, so "../../evil" lands in
// base/srv/evil — inside this test's own sandbox, outside the root.
func a199LayoutTree(t *testing.T) (base, root, foreign string) {
	t.Helper()
	base = t.TempDir()
	root = filepath.Join(base, "srv", "lib", "volumes")
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatalf("volume root: %v", err)
	}
	foreign = filepath.Join(base, "srv", "evil")
	if got := filepath.Clean(filepath.Join(root, "../../evil")); got != foreign {
		t.Fatalf("harness assumption broken: traversal lands at %s, not %s", got, foreign)
	}
	return base, root, foreign
}

func a199LayoutPlant(t *testing.T, dir, relID string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatalf("plant dir: %v", err)
	}
	m := Manifest{VolumeID: relID, Name: "planted", Cipher: "aes-128-gcm(fallback,non-GM)",
		DekRef: "vkey-0f1e2d3c4b5a69788796a5b4c3d2e1f0", AAD: "planted", HasData: true, DataSizeBytes: 7}
	b, err := json.Marshal(&m)
	if err != nil {
		t.Fatalf("encode planted manifest: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), b, 0600); err != nil {
		t.Fatalf("plant manifest: %v", err)
	}
}

// The layout reader must refuse an id that points outside VolumeRoot instead of
// handing back somebody else's manifest.
func TestA199_LayoutReaderRefusesEscapingID(t *testing.T) {
	k, srv := go01MockKMS(t)
	_, root, foreign := a199LayoutTree(t)
	p := go01Plugin(t, root, k, srv)
	a199LayoutPlant(t, foreign, "../../evil")

	for _, id := range a199LayoutEscapes {
		m, err := p.LoadManifest(id)
		if err == nil {
			t.Fatalf("loadManifest(%q) returned volume_id=%s dek_ref=%s from outside %s",
				id, m.VolumeID, m.DekRef, root)
		}
		if !strings.Contains(err.Error(), "volume_id") {
			t.Errorf("loadManifest(%q): rejection must name volume_id, got %v", id, err)
		}
	}
	if _, err := os.Stat(filepath.Join(foreign, "manifest.json")); err != nil {
		t.Fatalf("the planted tree must be untouched: %v", err)
	}
}

// The layout writer must refuse to create a volume tree outside VolumeRoot.
func TestA199_LayoutWriterRefusesEscapingID(t *testing.T) {
	k, srv := go01MockKMS(t)
	base, root, _ := a199LayoutTree(t)
	p := go01Plugin(t, root, k, srv)

	for _, id := range a199LayoutEscapes {
		err := p.SaveManifest(&Manifest{VolumeID: id, Name: "x", Cipher: "aes-128-gcm(fallback,non-GM)",
			DekRef: "vkey-0f1e2d3c4b5a69788796a5b4c3d2e1f0"})
		if err == nil {
			t.Fatalf("saveManifest(volume_id=%q) was accepted", id)
		}
		if !strings.Contains(err.Error(), "volume_id") {
			t.Errorf("saveManifest(%q): rejection must name volume_id, got %v", id, err)
		}
		if strings.Contains(err.Error(), "\n") {
			t.Errorf("saveManifest(%q): rejection must not echo a raw hostile id: %q", id, err.Error())
		}
	}
	if got := a199LayoutOutsideRoot(t, base, root); got != "" {
		t.Fatalf("saveManifest wrote outside the volume root:\n%s", got)
	}
}

// a199LayoutOutsideRoot lists every manifest.json under base that is not under root.
func a199LayoutOutsideRoot(t *testing.T, base, root string) string {
	t.Helper()
	var out strings.Builder
	err := filepath.Walk(base, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if !info.IsDir() && filepath.Base(path) == "manifest.json" && !a199LayoutInside(root, path) {
			out.WriteString(path)
			out.WriteString("\n")
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk %s: %v", base, err)
	}
	return out.String()
}

// paths() is the aggregation point: for an escaping id it must not hand out any
// path that resolves outside VolumeRoot.
func TestA199_LayoutPathsHandOutNothingOutsideRoot(t *testing.T) {
	k, srv := go01MockKMS(t)
	_, root, _ := a199LayoutTree(t)
	p := go01Plugin(t, root, k, srv)

	for _, id := range a199LayoutEscapes {
		pth := p.Paths(id)
		for name, got := range map[string]string{"dir": pth.Dir, "mani": pth.Mani,
			"ct": pth.Ct, "nonce": pth.Nonce, "tag": pth.Tag} {
			if got == "" {
				continue
			}
			if !a199LayoutInside(root, got) {
				t.Errorf("paths(%q).%s = %s, outside VolumeRoot %s", id, name, got, root)
			}
		}
	}
}

// A legitimate single-segment id must keep working through the layout layer —
// the guard is containment, not a second whitelist.
func TestA199_LayoutStillServesLegitimateID(t *testing.T) {
	k, srv := go01MockKMS(t)
	_, root, _ := a199LayoutTree(t)
	p := go01Plugin(t, root, k, srv)

	const id = "cds-data-0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
	if err := os.MkdirAll(filepath.Join(root, id), 0700); err != nil {
		t.Fatalf("mkdir volume dir: %v", err)
	}
	m := &Manifest{VolumeID: id, Name: "cds-data-x", Cipher: "aes-128-gcm(fallback,non-GM)",
		DekRef: "vkey-0f1e2d3c4b5a69788796a5b4c3d2e1f0", AAD: "cds-data-x"}
	if err := p.SaveManifest(m); err != nil {
		t.Fatalf("saveManifest(%q): %v", id, err)
	}
	got, err := p.LoadManifest(id)
	if err != nil || got.DekRef != m.DekRef {
		t.Fatalf("loadManifest(%q) = (%+v, %v)", id, got, err)
	}
	if pth := p.Paths(id); !a199LayoutInside(root, pth.Mani) {
		t.Fatalf("legitimate id resolved outside the root: %s", pth.Mani)
	}
}

func a199LayoutInside(root, path string) bool {
	clean := filepath.Clean(root)
	return path == clean || strings.HasPrefix(path, clean+string(os.PathSeparator))
}
