package vol_test

import . "cds-volume-plugin/internal/vol"

// A175b — the GM helper boot gate (config.go leg, go-02c handoff).
//
// The tongsuo helper is fed the PLAINTEXT DEK on stdin, so which binary that is
// must never be a PATH-resolution decision and must never rest in actor-writable
// storage. Config.Validate() previously never looked at HelperPath at all (grep:
// config.go had only the LoadConfig assignment), so a host/cubelet launch with
// the shipped bare default handed the DEK to whatever `sm4_gcm` PATH resolves.
// Boot must REFUSE (fail-closed, never a WARNING) when the helper is
//   - not an absolute path (exec.LookPath would search PATH),
//   - owned by a non-root uid (the owner can swap the binary),
//   - group- or world-writable (the root-owner exemption is void).
//
// Boundary, locked by the sibling A202③ gate (a202_config_test.go) and by
// TestA175B_MissingAbsoluteHelper below: a MISSING absolute helper stays a
// boot/deploy fact, not a config refusal — exec never searches PATH for an
// absolute path, so cipher.go already fails every Seal/Open closed, and the
// image pins presence with `test -x` (Dockerfile, A175C gate).

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func a175bConfig(helper string) Config {
	return Config{
		VolumeRoot:     "/var/lib/cds/volumes",
		SocketPath:     "/run/cds-volume-plugin/cds.sock",
		Backend:        "tongsuo",
		HelperPath:     helper,
		SubprocTimeout: 10 * time.Second,
		MaxVolumeBytes: 1 << 30,
		KMSToken:       "a175b-token",
	}
}

func TestA175B_ValidateRefusesNonAbsoluteHelper(t *testing.T) {
	// The shipped default is exactly the first entry: LoadConfig's fallback for
	// CDS_VOLUME_PLUGIN_SM4_HELPER is the bare name the finding names.
	for _, bad := range []string{"sm4_gcm", "./sm4_gcm", "bin/sm4_gcm", "../sm4gcm/sm4_gcm"} {
		err := a175bConfig(bad).Validate()
		if err == nil {
			t.Fatalf("Validate() accepted helper path %q: a non-absolute path is resolved by "+
				"exec against PATH, and the helper is fed the plaintext DEK on stdin — a "+
				"user-writable PATH dir on the node turns every Seal/Open into a DEK handover (A175)", bad)
		}
		if !strings.Contains(err.Error(), "CDS_VOLUME_PLUGIN_SM4_HELPER") {
			t.Errorf("the refusal must name the env knob the operator has to fix, got: %v", err)
		}
	}
}

func TestA175B_ValidateRefusesHelperNotOwnedByRoot(t *testing.T) {
	dir := t.TempDir()
	h := filepath.Join(dir, "sm4_gcm")
	// 0755: executable, NOT group/world-writable — the only remaining problem is
	// that this test run's uid (non-root) owns the file and could replace it.
	if err := os.WriteFile(h, []byte("#!/bin/sh\nexit 1\n"), 0o755); err != nil {
		t.Fatalf("write helper: %v", err)
	}
	if uid := os.Getuid(); uid == 0 {
		t.Skip("running as root: the ownership refusal cannot be produced on this host")
	}
	err := a175bConfig(h).Validate()
	if err == nil {
		t.Fatalf("Validate() accepted a helper owned by uid %d: whoever owns the file can "+
			"replace the binary that receives the plaintext DEK (A175)", os.Getuid())
	}
	if !strings.Contains(err.Error(), "CDS_VOLUME_PLUGIN_SM4_HELPER") {
		t.Errorf("the refusal must name the env knob, got: %v", err)
	}
}

func TestA175B_MissingAbsoluteHelperStaysConfigLegal(t *testing.T) {
	// Honest boundary of this gate, matching the locked A202③ behaviour (the
	// sibling CLI test reaches the crypto path with /nonexistent/sm4_gcm): an
	// absolute-but-absent helper exec-fails closed in cipher.go, it is not the
	// PATH-resolution hole A175 describes. Pinned so a later tightening here
	// cannot silently change what boots.
	if err := a175bConfig("/nonexistent/sm4_gcm").Validate(); err != nil {
		t.Fatalf("a missing ABSOLUTE helper was refused as a config error: %v — exec never "+
			"searches PATH for an absolute path, so this must stay a boot-time fact, not a "+
			"Validate() refusal (boundary with a202_config_test)", err)
	}
}

// fakeInfo lets the uid==0-and-writable arm — impossible to manufacture without
// root — be exercised against the real extraction code, with a real
// *syscall.Stat_t so the mode arithmetic itself is executed, not assumed.
type fakeInfo struct {
	mode os.FileMode
	sys  any
}

func (f fakeInfo) Name() string       { return "sm4_gcm" }
func (f fakeInfo) Size() int64        { return 1 }
func (f fakeInfo) Mode() os.FileMode  { return f.mode }
func (f fakeInfo) ModTime() time.Time { return time.Time{} }
func (f fakeInfo) IsDir() bool        { return false }
func (f fakeInfo) Sys() any           { return f.sys }

func TestA175B_HelperOwnershipPredicateArms(t *testing.T) {
	uid, writable, ok := HelperOwnership(fakeInfo{mode: 0o757, sys: &syscall.Stat_t{Uid: 0}})
	if !ok || uid != 0 || !writable {
		t.Fatalf("root-owned 0757 must read as uid 0 + writable-by-others, got uid=%d writable=%v ok=%v", uid, writable, ok)
	}
	uid, writable, ok = HelperOwnership(fakeInfo{mode: 0o755, sys: &syscall.Stat_t{Uid: 0}})
	if !ok || uid != 0 || writable {
		t.Fatalf("root-owned 0755 must read as compliant, got uid=%d writable=%v ok=%v", uid, writable, ok)
	}
	uid, writable, ok = HelperOwnership(fakeInfo{mode: 0o755, sys: &syscall.Stat_t{Uid: 1000}})
	if !ok || uid == 0 || writable {
		t.Fatalf("uid 1000 must read as non-root, got uid=%d writable=%v ok=%v", uid, writable, ok)
	}
	// An unknown stat shape must fail closed: unknown owner is not root owner.
	if _, _, ok := HelperOwnership(fakeInfo{mode: 0o755, sys: "not a stat"}); ok {
		t.Fatal("HelperOwnership reported ok for a non-unix Sys()")
	}
}

func TestA175B_RootOwnedHelperOnThisHostPasses(t *testing.T) {
	// Positive arm against a real host file rather than a fixture: the helper the
	// image ships is root-owned and 0755-ish, so the gate must not reject that
	// shape. Candidates probed in order; the first root-owned, non-writable one
	// the host actually has is used (expected values derived from stat, never
	// hardcoded).
	for _, cand := range []string{"/usr/bin/env", "/usr/bin/true", "/bin/sh"} {
		fi, err := os.Stat(cand)
		if err != nil {
			continue
		}
		uid, writable, ok := HelperOwnership(fi)
		if !ok || uid != 0 || writable {
			continue
		}
		if err := ValidateGMHelperPath(cand); err != nil {
			t.Fatalf("the gate rejected a root-owned, non-writable, absolute helper %s: %v", cand, err)
		}
		return
	}
	t.Fatal("host has no root-owned non-writable executable candidate — cannot exercise the positive arm")
}
