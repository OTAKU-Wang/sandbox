package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
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
	dir   string
	mani  string
	ct    string
	nonce string
	tag   string
}

func (p volumePaths) exists() bool {
	_, err := os.Stat(p.mani)
	return err == nil
}

type ErrVolumeNotFound struct{ ID string }

func (e ErrVolumeNotFound) Error() string { return "volume not found: " + e.ID }
func IsVolumeNotFound(err error) bool {
	var e ErrVolumeNotFound
	return errors.As(err, &e)
}

func (r *Plugin) paths(volumeID string) volumePaths {
	dir := filepath.Join(r.cfg.VolumeRoot, volumeID)
	return volumePaths{
		dir:   dir,
		mani:  filepath.Join(dir, "manifest.json"),
		ct:    filepath.Join(dir, "data.ct"),
		nonce: filepath.Join(dir, "data.nonce"),
		tag:   filepath.Join(dir, "data.tag"),
	}
}

func (r *Plugin) loadManifest(volumeID string) (*Manifest, error) {
	p := r.paths(volumeID)
	if !p.exists() {
		return nil, ErrVolumeNotFound{ID: volumeID}
	}
	b, err := os.ReadFile(p.mani)
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

func (r *Plugin) saveManifest(m *Manifest) error {
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	return writeAtomic(r.paths(m.VolumeID).mani, b, 0600)
}

func writeAtomic(path string, data []byte, perm os.FileMode) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, perm); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// stageDir returns the staging path <base>/cds-<volume_id> and verifies it
// stays inside base (self-proof of the upstream host_path containment rule).
func stageDir(baseDir, volumeID string) (string, error) {
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
