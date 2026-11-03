package vol_test

import . "cds-volume-plugin/internal/vol"

// A204①: the plugin image is debian:bookworm-slim, which ships NO CA root store,
// so an https CDS_VOLUME_PLUGIN_KMS_BASE_URL passes every build gate and then
// fails every DEK call with "certificate signed by unknown authority" — and the
// KMS client is fail-closed with no offline mode, so CreateVolume never works.
// A204②: the GM (Tongsuo SM4-GCM) backend is only exercised when a conditional
// os.Stat finds the helper, with neither t.Skip nor t.Fatal, so the gate can be
// green while the national-crypto path has never run.

import (
	"bytes"
	"crypto/rand"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// a204Helper returns the SM4-GCM helper binary, refusing the test — never
// skipping it — when the gate has no GM coverage to report (A204②).
func a204Helper(t *testing.T) string {
	t.Helper()
	for _, cand := range []string{
		filepath.Join("..", "..", "sm4gcm", "sm4_gcm"),
		filepath.Join("..", "..", "bin", "sm4_gcm"),
		filepath.Join("sm4gcm", "sm4_gcm"),
		filepath.Join("bin", "sm4_gcm"),
	} {
		if fi, err := os.Stat(cand); err == nil && !fi.IsDir() {
			return cand
		}
	}
	t.Fatalf("no SM4-GCM helper found next to this test: the national-crypto backend would be silently untested. " +
		"Build it first (cd cds-volume-plugin && make helper); refusing to report a green gate for GM (A204②)")
	return ""
}

func a204KeyNonce(t *testing.T) ([]byte, []byte) {
	t.Helper()
	key := make([]byte, DekLen)
	nonce := make([]byte, NonceLen)
	if _, err := rand.Read(key); err != nil {
		t.Fatalf("key: %v", err)
	}
	if _, err := rand.Read(nonce); err != nil {
		t.Fatalf("nonce: %v", err)
	}
	return key, nonce
}

// TestA204_GMBackendRoundTripsAndRefusesTampering runs the negative matrix the
// silent-skip site was supposed to run, against the real vendored-Tongsuo helper.
func TestA204_GMBackendRoundTripsAndRefusesTampering(t *testing.T) {
	helper := a204Helper(t)
	c := NewTongsuoCipherTimeout(helper, 30*time.Second)
	if !strings.Contains(c.Name(), "sm4") {
		t.Fatalf("backend name %q does not identify an SM4 construction", c.Name())
	}
	if err := RunSelfTest(c); err != nil {
		t.Fatalf("GM self-test through %s: %v", helper, err)
	}

	key, nonce := a204KeyNonce(t)
	aad := []byte("cds-data-a204")
	pt := bytes.Repeat([]byte("gm-payload-0123456789"), 40)

	ct, tag, err := c.Seal(key, nonce, aad, pt)
	if err != nil {
		t.Fatalf("seal: %v", err)
	}
	if bytes.Equal(ct, pt) {
		t.Fatal("the GM helper returned the plaintext as ciphertext")
	}
	got, err := c.Open(key, nonce, aad, ct, tag)
	if err != nil || !bytes.Equal(got, pt) {
		t.Fatalf("open: got %q err %v", got, err)
	}

	badCT := append([]byte(nil), ct...)
	badCT[0] ^= 0x01
	badTag := append([]byte(nil), tag...)
	badTag[len(badTag)-1] ^= 0x80
	otherKey, _ := a204KeyNonce(t)

	for _, tc := range []struct {
		Name            string
		k, n, a, c, tag []byte
	}{
		{"tampered ciphertext", key, nonce, aad, badCT, tag},
		{"tampered tag", key, nonce, aad, ct, badTag},
		{"aad mismatch", key, nonce, []byte("cds-data-other"), ct, tag},
		{"foreign key", otherKey, nonce, aad, ct, tag},
	} {
		if _, err := c.Open(tc.k, tc.n, tc.a, tc.c, tc.tag); err == nil {
			t.Errorf("%s: the GM backend accepted it (honest-degradation iron law: no plaintext on auth failure)", tc.Name)
		}
	}
}

func a204BaseConfig() Config {
	return Config{
		VolumeRoot:     "/var/lib/cds/volumes",
		SocketPath:     "/run/cds-volume-plugin/cds.sock",
		Backend:        "aes_fallback",
		SubprocTimeout: 10 * time.Second,
		MaxVolumeBytes: 1 << 30,
		KMSToken:       "a204-token",
	}
}

// TestA204_HTTPSKMSWithoutTrustRootsRefusesBoot: with no trust store reachable,
// booting would only produce per-request x509 failures.
func TestA204_HTTPSKMSWithoutTrustRootsRefusesBoot(t *testing.T) {
	t.Setenv("SSL_CERT_FILE", filepath.Join(t.TempDir(), "absent.pem"))
	t.Setenv("SSL_CERT_DIR", t.TempDir())

	cfg := a204BaseConfig()
	cfg.KMSBaseURL = "https://kms.internal:8443"
	err := cfg.Validate()
	if err == nil {
		t.Fatal("an https KMS endpoint with no trust store was accepted at boot: every DEK call would fail with x509 unknown-authority (A204①)")
	}
	for _, want := range []string{"ca-certificates", "CDS_VOLUME_PLUGIN_KMS_CA_FILE"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("the refusal must name %s (the two ways to fix it), got: %v", want, err)
		}
	}
}

// TestA204_HTTPSKMSWithTrustRootsBoots is the over-tightening guard: a reachable
// store, or a pinned bundle, must stay accepted.
func TestA204_HTTPSKMSWithTrustRootsBoots(t *testing.T) {
	bundle := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(bundle, []byte("-----BEGIN CERTIFICATE-----\nMA==\n-----END CERTIFICATE-----\n"), 0o600); err != nil {
		t.Fatalf("bundle: %v", err)
	}
	t.Setenv("SSL_CERT_FILE", bundle)
	t.Setenv("SSL_CERT_DIR", "")

	cfg := a204BaseConfig()
	cfg.KMSBaseURL = "https://kms.internal:8443"
	if err := cfg.Validate(); err != nil {
		t.Fatalf("a reachable trust store must boot: %v", err)
	}

	t.Setenv("SSL_CERT_FILE", filepath.Join(t.TempDir(), "absent.pem"))
	cfg.KMSCAFile = bundle
	if err := cfg.Validate(); err != nil {
		t.Fatalf("a pinned CDS_VOLUME_PLUGIN_KMS_CA_FILE must boot without a system store: %v", err)
	}
}

// TestA204_PlaintextKMSIsNotAffectedByTheTrustGate: loopback dev/http stays as A179
// left it (that half belongs to kms.go).
func TestA204_PlaintextKMSIsNotAffectedByTheTrustGate(t *testing.T) {
	t.Setenv("SSL_CERT_FILE", filepath.Join(t.TempDir(), "absent.pem"))
	t.Setenv("SSL_CERT_DIR", t.TempDir())
	cfg := a204BaseConfig()
	cfg.KMSBaseURL = "http://127.0.0.1:8000"
	if err := cfg.Validate(); err != nil {
		t.Fatalf("the http default must not be refused by the trust-root gate: %v", err)
	}
}
