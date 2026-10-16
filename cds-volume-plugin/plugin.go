package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// Plugin is the CDS encrypted volume plugin core (SS-10 CB-03).
// gRPC (server.go) and CLI (cli.go) are thin surfaces over these methods.
type Plugin struct {
	cfg    Config
	cipher Cipher
	kms    *KMSClient
}

func NewPlugin(cfg Config, c Cipher, kms *KMSClient) *Plugin {
	return &Plugin{cfg: cfg, cipher: c, kms: kms}
}

func validateVolumeID(id string) error {
	if id == "" || len(id) > 200 || strings.ContainsAny(id, "/\\") || id == "." || id == ".." {
		return fmt.Errorf("invalid volume_id %q", id)
	}
	return nil
}

func zeroize(b []byte) {
	for i := range b {
		b[i] = 0
	}
}

// CreateVolume generates a fresh volume DEK, registers it with the CDS KMS
// and materializes the ciphertext tree. Returns the private_data payload
// (<=1024B) that CDS puts into the sandbox annotation (spec §2.4): the
// VolumeSource.plugin_volume fallback carries NO private_data, so a volume
// created this way is only usable through the annotation path.
// Idempotent: a repeat Create for an existing volume returns its existing
// private_data.
func (r *Plugin) CreateVolume(ctx context.Context, volumeID, name string) (string, error) {
	if err := validateVolumeID(volumeID); err != nil {
		return "", err
	}
	if name == "" {
		name = volumeID
	}
	p := r.paths(volumeID)
	if m, err := r.loadManifest(volumeID); err == nil {
		return privateDataJSON(m.DekRef)
	} else if !IsVolumeNotFound(err) {
		return "", err
	}

	dek, err := randomBytes(dekLen)
	if err != nil {
		return "", err
	}
	ref, err := r.kms.RegisterDEK(ctx, volumeID, name, r.cipher.Name(), dek)
	zeroize(dek)
	if err != nil {
		return "", fmt.Errorf("volume DEK registration: %w (volume not created — fail-closed)", err)
	}

	if err := os.MkdirAll(p.dir, 0700); err != nil {
		return "", r.abortAfterRegister(ctx, volumeID, ref, err)
	}
	m := &Manifest{
		VolumeID:  volumeID,
		Name:      name,
		Cipher:    r.cipher.Name(),
		DekRef:    ref,
		AAD:       name, // spec: AAD = volume name/label
		CreatedAt: time.Now().UTC(),
	}
	if err := r.saveManifest(m); err != nil {
		return "", r.abortAfterRegister(ctx, volumeID, ref, err)
	}
	return privateDataJSON(ref)
}

// abortAfterRegister cleans up a half-created volume and revokes the
// registered DEK so no orphaned key survives a failed Create.
func (r *Plugin) abortAfterRegister(ctx context.Context, volumeID, ref string, cause error) error {
	if rvErr := r.kms.RevokeDEK(ctx, ref); rvErr != nil {
		log.Printf("FATAL: volume create failed AND DEK revoke failed (ref=%s): create=%v revoke=%v — manual KMS cleanup required", ref, cause, rvErr)
	}
	_ = os.RemoveAll(r.paths(volumeID).dir)
	return fmt.Errorf("volume create: %w", cause)
}

func privateDataJSON(ref string) (string, error) {
	b, err := json.Marshal(map[string]string{"session_dek_ref": ref})
	if err != nil {
		return "", err
	}
	if len(b) > 1024 {
		return "", fmt.Errorf("private_data exceeds 1024B limit (fail-closed)")
	}
	return string(b), nil
}

// ParsePrivateData extracts the KMS wrapped-key ref from a private_data
// payload (spec §2.4: annotation [{name, driver, private_data}]).
// An empty payload is an error — the fallback path has no private_data and
// cannot carry a DEK volume.
func ParsePrivateData(payload string) (ref string, err error) {
	if strings.TrimSpace(payload) == "" {
		return "", fmt.Errorf("private_data is empty: DEK volumes require the plugin-volume-sources annotation (fail-closed)")
	}
	var m map[string]string
	if err := json.Unmarshal([]byte(payload), &m); err != nil {
		return "", fmt.Errorf("private_data not valid JSON: %w", err)
	}
	ref = m["session_dek_ref"]
	if ref == "" {
		return "", fmt.Errorf("private_data missing session_dek_ref")
	}
	return ref, nil
}

// PutVolumeData SM4-GCM encrypts the data blob at rest. Single-shot: a
// second put is accepted only if byte-identical (idempotent retry).
func (r *Plugin) PutVolumeData(ctx context.Context, volumeID, aad string, data []byte) (uint64, bool, error) {
	m, err := r.loadManifest(volumeID)
	if err != nil {
		return 0, false, err
	}
	if int64(len(data)) > r.cfg.MaxVolumeBytes {
		return 0, false, fmt.Errorf("data exceeds max volume bytes %d", r.cfg.MaxVolumeBytes)
	}
	if aad == "" {
		aad = m.AAD
	}
	p := r.paths(volumeID)
	if m.HasData {
		existing, err := r.readStagedPlain(ctx, m, aad)
		if err != nil {
			return 0, false, fmt.Errorf("cannot verify existing data: %w (fail-closed)", err)
		}
		if bytes.Equal(existing, data) {
			return uint64(len(data)), true, nil
		}
		return 0, false, fmt.Errorf("volume %s already has data (idempotent retry must be byte-identical)", volumeID)
	}

	dek, err := r.unwrapDEK(ctx, m.DekRef, "volume-put:"+volumeID)
	if err != nil {
		return 0, false, err
	}
	nonce, err := randomBytes(nonceLen)
	if err != nil {
		zeroize(dek)
		return 0, false, err
	}
	ct, tag, err := r.cipher.Seal(dek, nonce, []byte(aad), data)
	if err != nil {
		zeroize(dek)
		return 0, false, fmt.Errorf("sm4-gcm seal: %w (fail-closed)", err)
	}
	zeroize(dek)

	if err := writeAtomic(p.ct, ct, 0600); err != nil {
		return 0, false, err
	}
	if err := writeAtomic(p.nonce, nonce, 0600); err != nil {
		_ = os.Remove(p.ct)
		return 0, false, err
	}
	if err := writeAtomic(p.tag, tag, 0600); err != nil {
		_ = os.Remove(p.ct)
		_ = os.Remove(p.nonce)
		return 0, false, err
	}
	m.HasData = true
	m.DataSizeBytes = uint64(len(data))
	if err := r.saveManifest(m); err != nil {
		return 0, false, err
	}
	return uint64(len(data)), false, nil
}

// Attach decrypts the volume into the staging dir inside volume_base_dir
// and returns the host_path (spec §2.4: host_path must be inside
// volume_base_dir; virtiofs RO into the VM).
//
// Flow: private_data → KMS unwrap DEK → SM4-GCM open (tag-verified) →
// stage file. Every failure is fail-closed: a volume that cannot be
// decrypted is an error, never plaintext.
func (r *Plugin) Attach(ctx context.Context, volumeID, baseDir, sandboxID, privateData string, refCount int64) (hostPath string, metadata map[string]string, err error) {
	m, err := r.loadManifest(volumeID)
	if err != nil {
		return "", nil, err
	}
	ref, err := ParsePrivateData(privateData)
	if err != nil {
		return "", nil, err
	}
	if ref != m.DekRef {
		return "", nil, fmt.Errorf("private_data session_dek_ref does not match volume manifest (fail-closed)")
	}
	hostPath, err = stageDir(baseDir, volumeID)
	if err != nil {
		return "", nil, err
	}
	stageFile := filepath.Join(hostPath, "data")

	if refCount > 0 {
		// dedup: a prior sandbox already staged this volume — reuse.
		if st, serr := os.Stat(stageFile); serr != nil || st.Size() == 0 {
			// stage lost (e.g. manual rm): re-stage below.
		} else {
			return hostPath, attachMetadata(m, baseDir), nil
		}
	}

	if !m.HasData {
		return "", nil, fmt.Errorf("volume %s has no data (PutVolumeData first)", volumeID)
	}

	dek, err := r.unwrapDEK(ctx, m.DekRef, "attach:"+sandboxID+":"+volumeID)
	if err != nil {
		return "", nil, fmt.Errorf("KMS DEK fetch: %w (fail-closed, Attach refused)", err)
	}
	p := r.paths(volumeID)
	nonce, err := os.ReadFile(p.nonce)
	if err != nil {
		zeroize(dek)
		return "", nil, err
	}
	tag, err := os.ReadFile(p.tag)
	if err != nil {
		zeroize(dek)
		return "", nil, err
	}
	ct, err := os.ReadFile(p.ct)
	if err != nil {
		zeroize(dek)
		return "", nil, err
	}
	plain, err := r.cipher.Open(dek, nonce, []byte(m.AAD), ct, tag)
	zeroize(dek)
	if err != nil {
		return "", nil, fmt.Errorf("sm4-gcm open: %w (fail-closed, no plaintext fallback)", err)
	}

	if err := os.MkdirAll(hostPath, 0700); err != nil {
		zeroize(plain)
		return "", nil, err
	}
	if err := writeAtomic(stageFile, plain, 0400); err != nil {
		zeroize(plain)
		return "", nil, err
	}
	zeroize(plain)
	return hostPath, attachMetadata(m, baseDir), nil
}

// attachMetadata is echoed back by Cubelet unchanged on Detach (proto
// contract); base_dir lets Detach locate the stage dir without a second
// control-plane round-trip.
func attachMetadata(m *Manifest, baseDir string) map[string]string {
	return map[string]string{
		"volume_id": m.VolumeID,
		"cipher":    m.Cipher,
		"base_dir":  baseDir,
		"staged":    "true",
	}
}

func (r *Plugin) unwrapDEK(ctx context.Context, ref, sessionID string) ([]byte, error) {
	return r.kms.UnwrapDEK(ctx, ref, sessionID)
}

// readStagedPlain decrypts an existing data blob (idempotency check path).
func (r *Plugin) readStagedPlain(ctx context.Context, m *Manifest, aad string) ([]byte, error) {
	p := r.paths(m.VolumeID)
	nonce, err := os.ReadFile(p.nonce)
	if err != nil {
		return nil, err
	}
	tag, err := os.ReadFile(p.tag)
	if err != nil {
		return nil, err
	}
	ct, err := os.ReadFile(p.ct)
	if err != nil {
		return nil, err
	}
	dek, err := r.unwrapDEK(ctx, m.DekRef, "verify:"+m.VolumeID)
	if err != nil {
		return nil, err
	}
	defer zeroize(dek)
	plain, err := r.cipher.Open(dek, nonce, []byte(aad), ct, tag)
	if err != nil {
		return nil, err
	}
	return plain, nil
}

// Detach removes the staging plaintext. With refCount == 0 the file is
// shredded (zeroed then unlinked) — spec: plaintext directory is wiped at
// session end; the ciphertext tree is untouched.
func (r *Plugin) Detach(ctx context.Context, volumeID, baseDir string, refCount int64) error {
	hostPath, err := stageDir(baseDir, volumeID)
	if err != nil {
		return err
	}
	stageFile := filepath.Join(hostPath, "data")
	if refCount > 0 {
		if _, serr := os.Stat(stageFile); serr != nil {
			return fmt.Errorf("ref_count>0 but stage file missing: %w (fail-closed)", serr)
		}
		return nil
	}
	fi, err := os.Stat(stageFile)
	if os.IsNotExist(err) {
		// already gone: best-effort rmdir, not an error.
		_ = os.Remove(hostPath)
		return nil
	}
	if err != nil {
		return err
	}
	if err := shredFile(stageFile, fi.Size()); err != nil {
		return fmt.Errorf("stage shred failed, plaintext retained: %w (fail-closed)", err)
	}
	_ = os.Remove(hostPath)
	return nil
}

// shredFile overwrites a file with zeros (best effort per chunk) then
// unlinks it. Returns the first hard error encountered. A read-only
// target (stage files are 0400) is made writable before shredding —
// the file is destroyed immediately afterwards.
func shredFile(path string, size int64) error {
	var firstErr error
	f, err := os.OpenFile(path, os.O_RDWR, 0)
	if err != nil && (errors.Is(err, syscall.EACCES) || errors.Is(err, syscall.EPERM)) {
		if cerr := os.Chmod(path, 0600); cerr != nil && firstErr == nil {
			firstErr = cerr
		}
		f, err = os.OpenFile(path, os.O_RDWR, 0)
	}
	if err != nil {
		return err
	}
	chunk := make([]byte, 1<<20)
	for off := int64(0); off < size; off += int64(len(chunk)) {
		n := int64(len(chunk))
		if size-off < n {
			n = size - off
		}
		if _, err := f.WriteAt(chunk[:n], off); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	if err := f.Close(); err != nil && firstErr == nil {
		firstErr = err
	}
	if err := os.Remove(path); err != nil && firstErr == nil {
		firstErr = err
	}
	return firstErr
}

// Destroy revokes the volume DEK at the KMS (crypto-erase: the wrapped key
// is destroyed first), then wipes the ciphertext tree. If KMS revoke fails
// the files are RETAINED — deleting data with a live DEK would leave the
// key dangling (fail-closed, spec: Destroy → crypto-erase → audit).
func (r *Plugin) Destroy(ctx context.Context, volumeID string) error {
	m, err := r.loadManifest(volumeID)
	if err != nil {
		return err
	}
	if err := r.kms.RevokeDEK(ctx, m.DekRef); err != nil {
		return fmt.Errorf("DEK revoke failed, volume data RETAINED: %w (fail-closed)", err)
	}
	p := r.paths(volumeID)
	for _, f := range []string{p.ct, p.nonce, p.tag, p.mani} {
		fi, serr := os.Stat(f)
		if serr != nil {
			continue
		}
		_ = shredFile(f, fi.Size())
	}
	if err := os.Remove(p.dir); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("volume dir removal: %w", err)
	}
	return nil
}

// GetVolumeInfo reports metadata for observability (spec step 5: data
// volume read-only observability; full plaintext is never returned).
func (r *Plugin) GetVolumeInfo(volumeID string) (*Manifest, error) {
	return r.loadManifest(volumeID)
}
