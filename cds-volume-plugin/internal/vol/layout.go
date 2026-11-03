package vol

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// On-disk layout for one volume (at-rest ciphertext tree):
//
//	<VolumeRoot>/<volume_id>/
//	  manifest.json   {volume_id, name, cipher, dek_ref, aad, created_at,
//	                   data_size_bytes, has_data}
//	  data.ct         SM4-GCM ciphertext
//	  data.nonce      12-byte GCM nonce
//	  data.tag        16-byte GCM tag
//
// Staging (only for the duration of an Attach, inside Cubelet's
// volume_base_dir, per the upstream hard constraint host_path ⊂ base_dir):
//
//	<volume_base_dir>/cds-<volume_id>/data   decrypted plaintext (0400)
type Manifest struct {
	VolumeID      string    `json:"volume_id"`
	Name          string    `json:"name"`
	Cipher        string    `json:"cipher"`
	DekRef        string    `json:"dek_ref"`
	AAD           string    `json:"aad"`
	CreatedAt     time.Time `json:"created_at"`
	DataSizeBytes uint64    `json:"data_size_bytes"`
	HasData       bool      `json:"has_data"`
}

type volumePaths struct {
	Dir   string
	Mani  string
	Ct    string
	Nonce string
	Tag   string
}

func (p volumePaths) Exists() bool {
	_, err := os.Stat(p.Mani)
	return err == nil
}

type ErrVolumeNotFound struct{ ID string }

func (e ErrVolumeNotFound) Error() string { return "volume not found: " + e.ID }
func IsVolumeNotFound(err error) bool {
	var e ErrVolumeNotFound
	return errors.As(err, &e)
}

// underVolumeRoot proves that volumeID resolves to a directory strictly inside
// VolumeRoot. volume_id is delivered over the socket by the control plane and
// paths() is the one place it becomes a host directory, so this containment
// proof is the layout layer's own last line of defence (A199) — it does not
// re-implement the RPC-boundary whitelist in plugin.go, it only refuses the one
// thing that must never happen: a volume directory outside the root.
func (r *Plugin) underVolumeRoot(volumeID string) bool {
	root := filepath.Clean(r.Cfg.VolumeRoot)
	dir := filepath.Join(root, volumeID)
	rel, err := filepath.Rel(root, dir)
	if err != nil {
		return false
	}
	return rel != "." && rel != ".." &&
		!strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}

// errVolumeOutsideRoot names the reason without echoing the id: volume_id is
// hostile input here, and a raw id in a message can forge a log line.
var errVolumeOutsideRoot = errors.New(
	"invalid volume_id: does not resolve to a directory inside the volume root (fail-closed)")

// paths resolves one volume's ciphertext tree. An id that escapes VolumeRoot
// gets NO usable path — every field stays empty, so a caller that skipped the
// entry-layer check lands on ENOENT instead of touching a file outside the root.
func (r *Plugin) Paths(volumeID string) volumePaths {
	if !r.underVolumeRoot(volumeID) {
		return volumePaths{}
	}
	dir := filepath.Join(r.Cfg.VolumeRoot, volumeID)
	return volumePaths{
		Dir:   dir,
		Mani:  filepath.Join(dir, "manifest.json"),
		Ct:    filepath.Join(dir, "data.ct"),
		Nonce: filepath.Join(dir, "data.nonce"),
		Tag:   filepath.Join(dir, "data.tag"),
	}
}

func (r *Plugin) LoadManifest(volumeID string) (*Manifest, error) {
	if !r.underVolumeRoot(volumeID) {
		return nil, errVolumeOutsideRoot
	}
	p := r.Paths(volumeID)
	if !p.Exists() {
		return nil, ErrVolumeNotFound{ID: volumeID}
	}
	b, err := os.ReadFile(p.Mani)
	if err != nil {
		return nil, err
	}
	var m Manifest
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, fmt.Errorf("manifest corrupt: %w", err)
	}
	if m.VolumeID != volumeID {
		return nil, fmt.Errorf("manifest volume_id mismatch (fail-closed)")
	}
	return &m, nil
}

func (r *Plugin) SaveManifest(m *Manifest) error {
	if !r.underVolumeRoot(m.VolumeID) {
		return errVolumeOutsideRoot
	}
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	return WriteAtomic(r.Paths(m.VolumeID).Mani, b, 0600)
}

// volumeWriteLocks serialises the writers that share a directory — in practice
// the writes of one volume's tree — inside this process, so two concurrent
// requests for the same volume cannot interleave (A198). Sharded instead of a
// map of per-volume mutexes so the table cannot grow with the volume count: two
// volumes that hash to one shard merely contend, they never interleave a write.
const volumeWriteLockShards = 64

var volumeWriteLocks [volumeWriteLockShards]sync.Mutex

func lockVolumeWrite(dir string) func() {
	h := uint32(2166136261)
	for i := 0; i < len(dir); i++ {
		h ^= uint32(dir[i])
		h *= 16777619
	}
	mu := &volumeWriteLocks[h%volumeWriteLockShards]
	mu.Lock()
	return mu.Unlock
}

// WriteAtomic publishes data at path so a reader only ever sees a complete old
// or new file: an exclusively-created temp in the target's own directory (the
// rename must stay on one filesystem), fsynced before the rename, the directory
// fsynced after it, and the temp removed on every exit path.
//
// A198 (OCR 2026-10-18): the previous version reused `path + ".tmp"`, so two
// concurrent writers of one volume clobbered each other's temp; a write that
// died between WriteFile and Rename left a 0400 temp that made every later
// write of that file fail (such a volume could never be Attach'd again); and
// without O_EXCL/O_NOFOLLOW a temp planted in advance was written straight
// through. CreateTemp gives a unique name with O_CREAT|O_EXCL, so a pre-planted
// `x.tmp` is skipped rather than followed.
func WriteAtomic(path string, data []byte, perm os.FileMode) error {
	// Every legitimate target is absolute (VolumeRoot must be absolute, and
	// StageDir refuses a relative volume_base_dir), so a relative target means
	// an id that failed containment above — never write relative to the CWD.
	if !filepath.IsAbs(path) {
		return fmt.Errorf("refusing to write %q outside an absolute root (fail-closed)", filepath.Base(path))
	}
	dir := filepath.Dir(path)
	unlock := lockVolumeWrite(dir)
	defer unlock()

	tmp, err := os.CreateTemp(dir, filepath.Base(path)+".tmp*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()

	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	// Durable before visible: the bytes must reach the device before the rename
	// makes them reachable, or a power loss leaves an empty/zero-length manifest.
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(perm); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return err
	}
	// The rename itself must survive the power loss too. File.Sync already
	// swallows ENOSYS/ENOTSUP/EPERM, so a filesystem without directory fsync
	// does not fail a write here.
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

// StageDir returns the staging path <base>/cds-<volume_id> and verifies it
// stays inside base (self-proof of the upstream host_path containment rule).
func StageDir(baseDir, volumeID string) (string, error) {
	// the Attach request's volume_base_dir is a host path and must be absolute
	// as delivered; resolving a relative path against the plugin's CWD would
	// silently move the stage tree somewhere Cubelet never looks.
	if !filepath.IsAbs(baseDir) {
		return "", fmt.Errorf("volume_base_dir must be absolute: %q", baseDir)
	}
	base, err := filepath.Abs(baseDir)
	if err != nil {
		return "", err
	}
	d := filepath.Join(base, "cds-"+volumeID)
	rel, err := filepath.Rel(base, d)
	if err != nil || rel == ".." || (len(rel) > 2 && rel[:3] == "../") {
		return "", fmt.Errorf("host_path escapes volume_base_dir (fail-closed)")
	}
	return d, nil
}
