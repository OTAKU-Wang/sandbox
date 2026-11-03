package vol

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
	Cfg    Config
	cipher Cipher
	Kms    *KMSClient
}

func NewPlugin(cfg Config, c Cipher, kms *KMSClient) *Plugin {
	return &Plugin{Cfg: cfg, cipher: c, Kms: kms}
}

// Classification sentinels for the two rejection classes this layer produces. The gRPC
// layer had to classify them by message prefix (server.go InputRejectionPrefixes, A199)
// because plugin.go exposed no typed error — so rewording a message here silently
// reclassifies a bad request as FailedPrecondition, which Cubelet reads as "retry later".
// errors.Is is now the classifier; the message stays for the operator.
var (
	// ErrInvalidVolumeID: volume_id is not one [A-Za-z0-9._-] path segment.
	ErrInvalidVolumeID = errors.New("invalid volume_id")
	// ErrVolumeNotContained: well-formed id that does not resolve inside VolumeRoot.
	ErrVolumeNotContained = errors.New("volume_id does not resolve to one directory under the volume root")
)

// VolumeNotContainedError carries the id for the audit log while staying
// errors.Is()-able against ErrVolumeNotContained (same shape as sandboxPathError).
// The message is byte-identical to the pre-sentinel one: server.go's interim
// classification and the a199 MapErr table are keyed on it.
type VolumeNotContainedError struct{ ID string }

func (e *VolumeNotContainedError) Error() string {
	return fmt.Sprintf("volume_id %q does not resolve to one directory under the volume root (fail-closed)", e.ID)
}

func (e *VolumeNotContainedError) Unwrap() error { return ErrVolumeNotContained }

// ValidateVolumeID is the RPC-boundary check for the one input that decides which
// directory on the host a request touches. A199 (OCR 2026-10-18): the id arrives
// over the socket from the control plane and is consumed only by paths(), so it has
// to be exactly one path segment. The whitelist is the shape the control plane
// actually sends — `cds-data-<session_id>` (cube_runtime_adapter.py:277,532,734,996).
func ValidateVolumeID(id string) error {
	if id == "" || len(id) > 200 {
		return fmt.Errorf("%w: %d byte(s), want 1-200 (fail-closed)", ErrInvalidVolumeID, len(id))
	}
	if id == "." || id == ".." || strings.Contains(id, "..") {
		return fmt.Errorf("%w %q: must be one path segment that does not traverse (fail-closed)", ErrInvalidVolumeID, id)
	}
	for i := 0; i < len(id); i++ {
		c := id[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
		case c == '-', c == '_', c == '.':
		default:
			// The offending byte is named, never the whole id: this input is hostile.
			return fmt.Errorf("%w: byte %d is %#x, outside [A-Za-z0-9._-] (fail-closed)", ErrInvalidVolumeID, i, c)
		}
	}
	return nil
}

// volumeChecked is the entry guard for every volume RPC: it validates the id AND
// proves the resolved directory is the direct child of VolumeRoot that this id
// names, so containment no longer rests on paths() cleaning the input (A199).
func (r *Plugin) volumeChecked(volumeID string) (volumePaths, error) {
	if err := ValidateVolumeID(volumeID); err != nil {
		return volumePaths{}, err
	}
	p := r.Paths(volumeID)
	rel, err := filepath.Rel(r.Cfg.VolumeRoot, p.Dir)
	if err != nil || rel != volumeID {
		return volumePaths{}, &VolumeNotContainedError{ID: volumeID}
	}
	return p, nil
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
	p, err := r.volumeChecked(volumeID)
	if err != nil {
		return "", err
	}
	if name == "" {
		name = volumeID
	}
	if m, err := r.LoadManifest(volumeID); err == nil {
		return privateDataJSON(m.DekRef)
	} else if !IsVolumeNotFound(err) {
		return "", err
	}

	dek, err := RandomBytes(DekLen)
	if err != nil {
		return "", err
	}
	ref, err := r.Kms.RegisterDEK(ctx, volumeID, name, r.cipher.Name(), dek)
	zeroize(dek)
	if err != nil {
		return "", fmt.Errorf("volume DEK registration: %w (volume not created — fail-closed)", err)
	}

	if err := os.MkdirAll(p.Dir, 0700); err != nil {
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
	if err := r.SaveManifest(m); err != nil {
		return "", r.abortAfterRegister(ctx, volumeID, ref, err)
	}
	return privateDataJSON(ref)
}

// abortAfterRegister cleans up a half-created volume and revokes the
// registered DEK so no orphaned key survives a failed Create.
func (r *Plugin) abortAfterRegister(ctx context.Context, volumeID, ref string, cause error) error {
	if rvErr := r.Kms.RevokeDEK(ctx, ref); rvErr != nil {
		log.Printf("FATAL: volume create failed AND DEK revoke failed (ref=%s): create=%v revoke=%v — manual KMS cleanup required", ref, cause, rvErr)
	}
	_ = os.RemoveAll(r.Paths(volumeID).Dir)
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
	if _, err := r.volumeChecked(volumeID); err != nil {
		return 0, false, err
	}
	m, err := r.LoadManifest(volumeID)
	if err != nil {
		return 0, false, err
	}
	if int64(len(data)) > r.Cfg.MaxVolumeBytes {
		return 0, false, fmt.Errorf("data exceeds max volume bytes %d", r.Cfg.MaxVolumeBytes)
	}
	if aad == "" {
		aad = m.AAD
	}
	p := r.Paths(volumeID)
	if m.HasData {
		// Manifest.AAD is the AAD the existing bytes were sealed with (Put writes
		// it back), so a retry that replays a different AAD is a caller error,
		// not something to paper over with a GCM auth failure.
		if aad != m.AAD {
			return 0, false, fmt.Errorf("volume %s is sealed under a different AAD (aad mismatch — fail-closed): "+
				"an idempotent retry must replay the AAD the data was sealed with", volumeID)
		}
		existing, err := r.readStagedPlain(ctx, m, aad)
		if err != nil {
			return 0, false, fmt.Errorf("cannot verify existing data: %w (fail-closed)", err)
		}
		// Decrypted comparison buffer: cleared on every exit, as Attach does (A202).
		defer zeroize(existing)
		if bytes.Equal(existing, data) {
			return uint64(len(data)), true, nil
		}
		return 0, false, fmt.Errorf("volume %s already has data (idempotent retry must be byte-identical)", volumeID)
	}

	dek, err := r.unwrapDEK(ctx, m.DekRef, "volume-put:"+volumeID)
	if err != nil {
		return 0, false, err
	}
	nonce, err := RandomBytes(NonceLen)
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

	if err := WriteAtomic(p.Ct, ct, 0600); err != nil {
		return 0, false, err
	}
	if err := WriteAtomic(p.Nonce, nonce, 0600); err != nil {
		_ = os.Remove(p.Ct)
		return 0, false, err
	}
	if err := WriteAtomic(p.Tag, tag, 0600); err != nil {
		_ = os.Remove(p.Ct)
		_ = os.Remove(p.Nonce)
		return 0, false, err
	}
	m.HasData = true
	m.DataSizeBytes = uint64(len(data))
	// Record the AAD that actually sealed these bytes. Attach and every later
	// Open use Manifest.AAD; leaving the create-time default here makes the whole
	// volume unreadable the moment a caller supplies its own AAD (A201).
	m.AAD = aad
	if err := r.SaveManifest(m); err != nil {
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
	if _, verr := r.volumeChecked(volumeID); verr != nil {
		return "", nil, verr
	}
	m, err := r.LoadManifest(volumeID)
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
	hostPath, err = StageDir(baseDir, volumeID)
	if err != nil {
		return "", nil, err
	}
	stageFile := filepath.Join(hostPath, "data")

	if refCount > 0 {
		// dedup: a prior sandbox already staged this volume — reuse.
		if st, serr := os.Stat(stageFile); serr != nil || st.Size() == 0 {
			// stage lost (e.g. manual rm): re-stage below.
		} else {
			r.recordStage(volumeID, hostPath)
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
	p := r.Paths(volumeID)
	nonce, err := os.ReadFile(p.Nonce)
	if err != nil {
		zeroize(dek)
		return "", nil, err
	}
	tag, err := os.ReadFile(p.Tag)
	if err != nil {
		zeroize(dek)
		return "", nil, err
	}
	ct, err := os.ReadFile(p.Ct)
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
	if err := WriteAtomic(stageFile, plain, 0400); err != nil {
		zeroize(plain)
		return "", nil, err
	}
	zeroize(plain)
	// The stage pointer is what lets Detach/Destroy find this cleartext later;
	// without it a caller that loses volume_base_dir leaves the plaintext behind.
	r.recordStage(volumeID, hostPath)
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
	return r.Kms.UnwrapDEK(ctx, ref, sessionID)
}

// readStagedPlain decrypts an existing data blob (idempotency check path).
func (r *Plugin) readStagedPlain(ctx context.Context, m *Manifest, aad string) ([]byte, error) {
	p := r.Paths(m.VolumeID)
	nonce, err := os.ReadFile(p.Nonce)
	if err != nil {
		return nil, err
	}
	tag, err := os.ReadFile(p.Tag)
	if err != nil {
		return nil, err
	}
	ct, err := os.ReadFile(p.Ct)
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
//
// The staging location is the one Attach recorded, not only the base_dir this
// caller happens to pass: a Detach whose base_dir is stale must still wipe the
// cleartext rather than report success with it still on disk (A200).
func (r *Plugin) Detach(ctx context.Context, volumeID, baseDir string, refCount int64) error {
	if _, err := r.volumeChecked(volumeID); err != nil {
		return err
	}
	hostPath, err := StageDir(baseDir, volumeID)
	if err != nil {
		return err
	}
	if err := r.detachStage(volumeID, hostPath, refCount); err != nil {
		return err
	}
	if refCount == 0 {
		r.clearStageRecord(volumeID)
	}
	return nil
}

func (r *Plugin) detachStage(volumeID, hostPath string, refCount int64) error {
	stageFile := filepath.Join(hostPath, "data")
	if refCount > 0 {
		if _, serr := os.Stat(stageFile); serr != nil {
			return fmt.Errorf("ref_count>0 but stage file missing: %w (fail-closed)", serr)
		}
		return nil
	}
	recorded := r.stagedDirOf(volumeID)
	if recorded != "" && filepath.Clean(recorded) != filepath.Clean(hostPath) {
		log.Printf("WARNING: Detach(%s) volume_base_dir %q is not where Attach staged the volume (%q) — both locations are wiped (fail-closed)",
			volumeID, hostPath, recorded)
		if err := r.wipeStagedPlaintext(recorded); err != nil {
			return err
		}
	}
	_, err := os.Stat(stageFile)
	if os.IsNotExist(err) {
		// already gone: best-effort rmdir, not an error.
		_ = os.Remove(hostPath)
		return nil
	}
	if err != nil {
		return err
	}
	return r.wipeStagedPlaintext(hostPath)
}

// stageRecord is the plugin-side pointer to the live staging dir of a volume.
// It lives inside the root-owned ciphertext tree, so only the plugin (root) can
// read or rewrite it — a sandbox tenant cannot redirect a shred.
type stageRecord struct {
	VolumeID string    `json:"volume_id"`
	StageDir string    `json:"stage_dir"`
	StagedAt time.Time `json:"staged_at"`
}

func (r *Plugin) stageRecordPath(volumeID string) string {
	return filepath.Join(r.Paths(volumeID).Dir, "stage.json")
}

func (r *Plugin) recordStage(volumeID, dir string) {
	b, err := json.Marshal(stageRecord{VolumeID: volumeID, StageDir: dir, StagedAt: time.Now().UTC()})
	if err != nil {
		log.Printf("WARNING: cannot encode stage record for %s: %v", volumeID, err)
		return
	}
	if err := WriteAtomic(r.stageRecordPath(volumeID), b, 0600); err != nil {
		log.Printf("WARNING: stage record for %s at %s not written: %v — Destroy/Detach can only find the plaintext through the caller's volume_base_dir",
			volumeID, volumeID, err)
	}
}

func (r *Plugin) stagedDirOf(volumeID string) string {
	b, err := os.ReadFile(r.stageRecordPath(volumeID))
	if err != nil {
		return ""
	}
	var rec stageRecord
	if json.Unmarshal(b, &rec) != nil || rec.VolumeID != volumeID {
		return ""
	}
	return rec.StageDir
}

func (r *Plugin) clearStageRecord(volumeID string) {
	_ = os.Remove(r.stageRecordPath(volumeID))
}

// wipeStagedPlaintext shreds <dir>/data and drops the now-empty stage dir. A
// stage that is already gone is not an error; a shred that fails is, and the
// cleartext is retained for retry.
func (r *Plugin) wipeStagedPlaintext(dir string) error {
	if dir == "" {
		return nil
	}
	stageFile := filepath.Join(dir, "data")
	fi, err := os.Stat(stageFile)
	if os.IsNotExist(err) {
		_ = os.Remove(dir)
		return nil
	}
	if err != nil {
		return err
	}
	if err := ShredFile(stageFile, fi.Size()); err != nil {
		return fmt.Errorf("stage shred failed, plaintext retained at %s: %w (fail-closed)", dir, err)
	}
	_ = os.Remove(dir)
	return nil
}

// ShredFile overwrites a file with zeros, flushes them to the device and only
// then unlinks it. A shred that failed anywhere along the way RETAINS the file
// and reports the error: unlinking after a failed zero-fill would claim the
// data is gone when its bytes were never destroyed (iron law "shred 失败要报错").
// A read-only target (stage files are 0400) is made writable before shredding —
// the file is destroyed immediately afterwards.
func ShredFile(path string, size int64) error {
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
	// Without this the zeroing may live only in the page cache while the unlink
	// already went to the journal — "shredded" would survive no power loss.
	if err := f.Sync(); err != nil && firstErr == nil {
		firstErr = err
	}
	if err := f.Close(); err != nil && firstErr == nil {
		firstErr = err
	}
	if firstErr != nil {
		return firstErr
	}
	return os.Remove(path)
}

// Destroy revokes the volume DEK at the KMS (crypto-erase: the wrapped key
// is destroyed first), then wipes the staged plaintext and the ciphertext tree.
// If KMS revoke fails the files are RETAINED — deleting data with a live DEK
// would leave the key dangling (fail-closed, spec: Destroy → crypto-erase →
// audit). A shred that fails is reported the same way: "destroyed" may never
// be returned for bytes that were not destroyed (A200).
func (r *Plugin) Destroy(ctx context.Context, volumeID string) error {
	if _, err := r.volumeChecked(volumeID); err != nil {
		return err
	}
	m, err := r.LoadManifest(volumeID)
	if err != nil {
		return err
	}
	staged := r.stagedDirOf(volumeID)
	if staged != "" {
		if _, serr := os.Stat(filepath.Join(staged, "data")); serr == nil {
			log.Printf("WARNING: Destroy(%s) while the volume still has staged plaintext at %s — "+
				"crypto-erase wipes it; an attached volume should be Detached first", volumeID, staged)
		}
	}
	if err := r.Kms.RevokeDEK(ctx, m.DekRef); err != nil {
		return fmt.Errorf("DEK revoke failed, volume data RETAINED: %w (fail-closed)", err)
	}
	p := r.Paths(volumeID)
	if err := r.wipeStagedPlaintext(staged); err != nil {
		return fmt.Errorf("crypto-erase incomplete, volume tree RETAINED for retry: %w (fail-closed)", err)
	}
	var shredErr error
	for _, f := range []string{p.Ct, p.Nonce, p.Tag, p.Mani, r.stageRecordPath(volumeID)} {
		fi, serr := os.Stat(f)
		if os.IsNotExist(serr) {
			continue
		}
		if serr != nil {
			shredErr = fmt.Errorf("%s: %w", f, serr)
			break
		}
		if herr := ShredFile(f, fi.Size()); herr != nil {
			shredErr = fmt.Errorf("%s: %w", f, herr)
			break
		}
	}
	if shredErr != nil {
		// Stop at the first failure: continuing would tear the tree down around a
		// file that could not be destroyed and leave no state to retry from.
		return fmt.Errorf("crypto-erase shred failed, volume tree RETAINED for retry: %w (fail-closed)", shredErr)
	}
	if err := os.Remove(p.Dir); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("volume dir removal: %w", err)
	}
	r.clearStageRecord(volumeID)
	return nil
}

// GetVolumeInfo reports metadata for observability (spec step 5: data
// volume read-only observability; full plaintext is never returned).
func (r *Plugin) GetVolumeInfo(volumeID string) (*Manifest, error) {
	if _, err := r.volumeChecked(volumeID); err != nil {
		return nil, err
	}
	return r.LoadManifest(volumeID)
}
