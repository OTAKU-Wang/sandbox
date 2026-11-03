package vol_test

import . "cds-volume-plugin/internal/vol"

// A198 (OCR 2026-10-18, medium-high) — the "atomic" write in layout.go used a
// fixed `path + ".tmp"`, never fsynced, never removed the temp on its failure
// path, and ran with no per-volume serialization. Four observable consequences
// are pinned here:
//
//  1. a leftover 0400 temp from a write that died between write and rename
//     poisons every later write of that file (EACCES) — the volume can never be
//     Attach'd again;
//  2. a failed write strands its temp in the volume tree forever;
//  3. a temp name planted in advance gets followed/overwritten (no O_EXCL,
//     no O_NOFOLLOW), so a pre-created `x.tmp` is a write primitive;
//  4. two concurrent writers of the same volume clobber each other's single
//     shared temp — one of them loses the rename and the manifest.
//
// fsync-before-rename (the durability half of the finding) is not observable
// from a test; it is recorded as such in the evidence file rather than faked.

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// A leftover `data.tmp` with mode 0400 is exactly what a crashed Attach leaves
// behind (stage plaintext is written 0400). The next Attach must not care.
func TestA198_StaleTmpMustNotPoisonAttach(t *testing.T) {
	k, srv := go01MockKMS(t)
	root := t.TempDir()
	p := go01Plugin(t, root, k, srv)
	ctx := context.Background()

	const id = "cds-data-a198-attach"
	const payload = "volume plaintext for the stale-tmp case\n"
	pd, err := p.CreateVolume(ctx, id, "cds-data-a198")
	if err != nil {
		t.Fatalf("CreateVolume: %v", err)
	}
	if _, _, err := p.PutVolumeData(ctx, id, "", []byte(payload)); err != nil {
		t.Fatalf("PutVolumeData: %v", err)
	}

	base := t.TempDir()
	stage, err := StageDir(base, id)
	if err != nil {
		t.Fatalf("StageDir: %v", err)
	}
	if err := os.MkdirAll(stage, 0700); err != nil {
		t.Fatalf("mkdir stage: %v", err)
	}
	poison := filepath.Join(stage, "data.tmp") // the fixed name WriteAtomic chose
	if err := os.WriteFile(poison, []byte("half-written plaintext"), 0400); err != nil {
		t.Fatalf("plant stale tmp: %v", err)
	}

	host, _, err := p.Attach(ctx, id, base, "sandbox-1", pd, 1)
	if err != nil {
		t.Fatalf("Attach refused because a stale 0400 temp sits next to the stage file: %v", err)
	}
	stageFile := filepath.Join(host, "data")
	got, err := os.ReadFile(stageFile)
	if err != nil {
		t.Fatalf("staged plaintext missing at %s: %v", stageFile, err)
	}
	if string(got) != payload {
		t.Errorf("staged plaintext = %q, want %q", got, payload)
	}
	// Convention: staged plaintext is 0400 — the fix chmods the temp it renames.
	if fi, serr := os.Stat(stageFile); serr != nil || fi.Mode().Perm() != 0400 {
		t.Errorf("stage plaintext mode = %v, want 0400", fi.Mode().Perm())
	}
}

// A write that fails must not leave a temp file behind in the volume tree.
func TestA198_FailedWriteLeavesNoTempBehind(t *testing.T) {
	dir := t.TempDir()
	// rename(2) onto a directory fails, which is the failure path that used to
	// strand the temp next to the target.
	target := filepath.Join(dir, "manifest.json")
	if err := os.Mkdir(target, 0700); err != nil {
		t.Fatalf("mkdir target: %v", err)
	}

	if err := WriteAtomic(target, []byte("manifest bytes"), 0600); err == nil {
		t.Fatalf("WriteAtomic onto a directory succeeded; the failure path is not covered")
	}

	ents, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("readdir: %v", err)
	}
	for _, e := range ents {
		if strings.Contains(e.Name(), ".tmp") {
			t.Errorf("failed write stranded %s in %s", filepath.Join(dir, e.Name()), dir)
		}
	}
}

// `x.tmp` created in advance (by anyone who can reach the directory) must not
// become the file WriteAtomic writes through: no O_EXCL/O_NOFOLLOW meant a
// planted temp was followed and overwritten.
func TestA198_PlantedTempIsNotWrittenThrough(t *testing.T) {
	dir := t.TempDir()
	victim := filepath.Join(dir, "victim")
	if err := os.WriteFile(victim, []byte("someone else's bytes"), 0600); err != nil {
		t.Fatalf("plant victim: %v", err)
	}
	target := filepath.Join(dir, "manifest.json")
	if err := os.Symlink(victim, target+".tmp"); err != nil {
		t.Fatalf("plant symlink temp: %v", err)
	}

	if err := WriteAtomic(target, []byte("manifest bytes"), 0600); err != nil {
		t.Fatalf("WriteAtomic: %v", err)
	}
	got, err := os.ReadFile(victim)
	if err != nil {
		t.Fatalf("read victim: %v", err)
	}
	if string(got) != "someone else's bytes" {
		t.Errorf("the planted temp was written through: victim now holds %q", got)
	}
	if b, rerr := os.ReadFile(target); rerr != nil || string(b) != "manifest bytes" {
		t.Errorf("target = %q (err %v), want the payload", b, rerr)
	}
}

// Concurrent writers of one volume must not clobber each other: every write
// succeeds and the manifest on disk is always one complete writer's value.
func TestA198_ConcurrentManifestWritesDoNotClobber(t *testing.T) {
	k, srv := go01MockKMS(t)
	root := t.TempDir()
	p := go01Plugin(t, root, k, srv)
	ctx := context.Background()

	const id = "cds-data-a198-concurrent"
	if _, err := p.CreateVolume(ctx, id, "cds-data-a198"); err != nil {
		t.Fatalf("CreateVolume: %v", err)
	}

	const (
		writers = 8
		rounds  = 5
		// A fat manifest widens the write/rename window so the fixed-name temp
		// collision is not left to luck on the unfixed code.
		fat = 256 << 10
	)

	var (
		mu      sync.Mutex
		errs    []error
		started sync.WaitGroup
		done    sync.WaitGroup
	)
	fire := make(chan struct{})
	for w := 0; w < writers; w++ {
		done.Add(1)
		started.Add(1)
		go func(w int) {
			defer done.Done()
			started.Done()
			<-fire
			for r := 0; r < rounds; r++ {
				m := &Manifest{
					VolumeID:      id,
					Name:          strings.Repeat("x", fat) + string(rune('a'+w)),
					Cipher:        "aes-128-gcm(fallback,non-GM)",
					DekRef:        "vkey-0f1e2d3c4b5a69788796a5b4c3d2e1f0",
					CreatedAt:     time.Now().UTC(),
					DataSizeBytes: uint64(w*rounds + r),
					HasData:       true,
				}
				if err := p.SaveManifest(m); err != nil {
					mu.Lock()
					errs = append(errs, err)
					mu.Unlock()
				}
			}
		}(w)
	}
	started.Wait()
	close(fire)
	done.Wait()

	if len(errs) > 0 {
		t.Fatalf("%d of %d concurrent saveManifest calls failed, first: %v",
			len(errs), writers*rounds, errs[0])
	}

	m, err := p.LoadManifest(id)
	if err != nil {
		t.Fatalf("manifest after concurrent writes is unreadable: %v", err)
	}
	if m.DataSizeBytes >= writers*rounds {
		t.Errorf("manifest DataSizeBytes = %d, outside the written range", m.DataSizeBytes)
	}
	if !strings.HasPrefix(m.Name, strings.Repeat("x", fat)) {
		t.Errorf("manifest Name is not a complete writer's value (%d bytes)", len(m.Name))
	}
	ents, err := os.ReadDir(filepath.Join(root, id))
	if err != nil {
		t.Fatalf("readdir volume dir: %v", err)
	}
	for _, e := range ents {
		if strings.Contains(e.Name(), ".tmp") {
			t.Errorf("concurrent writes stranded %s in the volume tree", e.Name())
		}
	}
}
