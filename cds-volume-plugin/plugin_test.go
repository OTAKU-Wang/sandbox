package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// mockKMS is an in-process stand-in for the CDS control-plane KMS
// (register/distribute/revoke volume DEKs).
type mockKMS struct {
	mu    sync.Mutex
	keys  map[string]string // ref -> dek hex
	ref   int
	fails int // first N unwraps fail (fault injection)
}

func newMockKMS() (*mockKMS, *httptest.Server) {
	m := &mockKMS{keys: map[string]string{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		m.mu.Lock()
		defer m.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/kms/volume-keys":
			var body struct {
				DEKHex string `json:"dek_hex"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			m.ref++
			ref := "vol-dek-ref-" + itoa(m.ref)
			m.keys[ref] = body.DEKHex
			_ = json.NewEncoder(w).Encode(map[string]string{"key_id": ref})
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/distribute"):
			ref := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/v1/kms/volume-keys/"), "/distribute")
			if m.fails > 0 {
				m.fails--
				http.Error(w, "injected failure", http.StatusInternalServerError)
				return
			}
			dek, ok := m.keys[ref]
			if !ok {
				http.Error(w, "unknown ref", http.StatusNotFound)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]string{"key_hex": dek})
		case r.Method == http.MethodDelete && strings.HasPrefix(r.URL.Path, "/api/v1/kms/volume-keys/"):
			ref := strings.TrimPrefix(r.URL.Path, "/api/v1/kms/volume-keys/")
			delete(m.keys, ref)
			w.WriteHeader(http.StatusNoContent)
		default:
			http.Error(w, "unexpected", http.StatusNotFound)
		}
	}))
	return m, srv
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

func testPlugin(t *testing.T, backend string, helperPath string) (*Plugin, *mockKMS, string, func()) {
	t.Helper()
	root := t.TempDir()
	var c Cipher
	switch backend {
	case "tongsuo":
		c = NewTongsuoCipher(helperPath, 30)
	default:
		c = NewAESGCMCipher()
	}
	if err := RunSelfTest(c); err != nil {
		t.Fatalf("self-test: %v", err)
	}
	m, srv := newMockKMS()
	cfg := Config{
		VolumeRoot:     root,
		Backend:        backend,
		MaxVolumeBytes: 1 << 30,
	}
	p := NewPlugin(cfg, c, NewKMSClient(srv.URL, "test-token"))
	return p, m, root, srv.Close
}

// happy path: Create → Put → Attach → plaintext verified → Detach shreds.
func TestLifecycleRoundTrip(t *testing.T) {
	for _, backend := range []string{"aes_fallback"} {
		t.Run(backend, func(t *testing.T) {
			p, m, _, done := testPlugin(t, backend, "")
			defer done()
			ctx := context.Background()
			base := t.TempDir()
			data := bytes.Repeat([]byte("cds-data-canary-0123456789\n"), 300)

			pd, err := p.CreateVolume(ctx, "vol-1", "cds-data-s1")
			if err != nil {
				t.Fatalf("create: %v", err)
			}
			var pdm map[string]string
			if err := json.Unmarshal([]byte(pd), &pdm); err != nil || pdm["session_dek_ref"] == "" {
				t.Fatalf("private_data bad: %s", pd)
			}
			if !strings.Contains(pd, "vol-dek-ref-1") {
				t.Fatalf("private_data ref mismatch: %s", pd)
			}

			// at-rest tree: no ciphertext files exist before Put
			vdir := filepath.Join(p.cfg.VolumeRoot, "vol-1")
			if _, err := os.Stat(filepath.Join(vdir, "manifest.json")); err != nil {
				t.Fatalf("manifest missing after create")
			}
			if _, err := os.Stat(filepath.Join(vdir, "data.ct")); !os.IsNotExist(err) {
				t.Fatalf("data.ct must not exist before PutVolumeData")
			}
			n, idem, err := p.PutVolumeData(ctx, "vol-1", "", data)
			if err != nil || n != uint64(len(data)) || idem {
				t.Fatalf("put: n=%d idem=%v err=%v", n, idem, err)
			}
			ct, err := os.ReadFile(filepath.Join(vdir, "data.ct"))
			if err != nil {
				t.Fatalf("data.ct missing: %v", err)
			}
			if bytes.Contains(ct, []byte("cds-data-canary")) {
				t.Fatal("plaintext leaked into at-rest ciphertext tree")
			}
			nonce, _ := os.ReadFile(filepath.Join(vdir, "data.nonce"))
			tag, _ := os.ReadFile(filepath.Join(vdir, "data.tag"))
			if len(nonce) != nonceLen || len(tag) != tagLen {
				t.Fatalf("nonce/tag sizes %d/%d", len(nonce), len(tag))
			}
			mani := filepath.Join(vdir, "manifest.json")
			if _, err := os.Stat(mani); err != nil {
				t.Fatalf("manifest missing")
			}

			// idempotent retry
			_, idem, err = p.PutVolumeData(ctx, "vol-1", "", data)
			if err != nil || !idem {
				t.Fatalf("idempotent put: idem=%v err=%v", idem, err)
			}
			// non-identical second put must fail
			if _, _, err := p.PutVolumeData(ctx, "vol-1", "", data[:100]); err == nil {
				t.Fatal("second put with different data must fail")
			}

			hostPath, meta, err := p.Attach(ctx, "vol-1", base, "sbx-1", pd, 0)
			if err != nil {
				t.Fatalf("attach: %v", err)
			}
			// host_path is the stage dir (virtiofs RO root → /data in-VM, spec §2.4)
			want := filepath.Join(base, "cds-vol-1")
			if hostPath != want {
				t.Fatalf("host_path %s != %s", hostPath, want)
			}
			if meta["base_dir"] != base {
				t.Fatalf("metadata base_dir missing: %v", meta)
			}
			stageFile := filepath.Join(want, "data")
			got, err := os.ReadFile(stageFile)
			if err != nil || !bytes.Equal(got, data) {
				t.Fatalf("staged plaintext mismatch: %v", err)
			}
			// stage file must be read-only
			fi, _ := os.Stat(stageFile)
			if fi.Mode().Perm() != 0400 {
				t.Fatalf("stage perm %v (want 0400)", fi.Mode().Perm())
			}

			// dedup attach (refCount>0)
			hp2, _, err := p.Attach(ctx, "vol-1", base, "sbx-2", pd, 1)
			if err != nil || hp2 != hostPath {
				t.Fatalf("dedup attach: %v %s", err, hp2)
			}

			// detach with refcount>0 keeps stage
			if err := p.Detach(ctx, "vol-1", base, 1); err != nil {
				t.Fatalf("detach refcount=1: %v", err)
			}
			if _, err := os.Stat(stageFile); err != nil {
				t.Fatalf("stage must survive refcount>0 detach")
			}
			if err := p.Detach(ctx, "vol-1", base, 0); err != nil {
				t.Fatalf("detach refcount=0: %v", err)
			}
			if _, err := os.Stat(stageFile); !os.IsNotExist(err) {
				t.Fatalf("stage file must be shredded at refcount=0")
			}

			if err := p.Destroy(ctx, "vol-1"); err != nil {
				t.Fatalf("destroy: %v", err)
			}
			if _, err := os.Stat(vdir); !os.IsNotExist(err) {
				t.Fatalf("volume dir must be removed after destroy")
			}
			if len(m.keys) != 0 {
				t.Fatalf("mock KMS must have no keys after destroy")
			}
		})
	}
}

// fail-closed: KMS outage at Attach → error, no stage file created.
func TestAttachFailClosedOnKMSFailure(t *testing.T) {
	p, m, _, done := testPlugin(t, "aes_fallback", "")
	defer done()
	ctx := context.Background()
	base := t.TempDir()
	pd, err := p.CreateVolume(ctx, "vol-kms", "cds-data-k")
	if err != nil {
		t.Fatal(err)
	}
	data := []byte("secret payload")
	if _, _, err := p.PutVolumeData(ctx, "vol-kms", "", data); err != nil {
		t.Fatal(err)
	}
	m.fails = 5
	if _, _, err := p.Attach(ctx, "vol-kms", base, "sbx", pd, 0); err == nil {
		t.Fatal("attach with KMS outage must fail (fail-closed)")
	}
	if _, err := os.Stat(filepath.Join(base, "cds-vol-kms", "data")); !os.IsNotExist(err) {
		t.Fatal("no stage file may exist after failed attach")
	}
}

// fail-closed: tampered at-rest ciphertext → Attach must refuse.
func TestAttachTamperedCiphertext(t *testing.T) {
	p, _, _, done := testPlugin(t, "aes_fallback", "")
	defer done()
	ctx := context.Background()
	base := t.TempDir()
	pd, _ := p.CreateVolume(ctx, "vol-t", "cds-data-t")
	data := []byte("tamper me please")
	if _, _, err := p.PutVolumeData(ctx, "vol-t", "", data); err != nil {
		t.Fatal(err)
	}
	ctPath := filepath.Join(p.cfg.VolumeRoot, "vol-t", "data.ct")
	ct, _ := os.ReadFile(ctPath)
	ct[0] ^= 0xFF
	os.WriteFile(ctPath, ct, 0600)
	if _, _, err := p.Attach(ctx, "vol-t", base, "sbx", pd, 0); err == nil {
		t.Fatal("attach with tampered ciphertext must fail (GCM integrity, fail-closed)")
	}
}

// private_data parsing (spec §2.4).
func TestParsePrivateData(t *testing.T) {
	if _, err := ParsePrivateData(""); err == nil {
		t.Fatal("empty private_data must fail (fallback path carries no DEK ref)")
	}
	if _, err := ParsePrivateData("{not json"); err == nil {
		t.Fatal("invalid JSON must fail")
	}
	if _, err := ParsePrivateData(`{"other":"x"}`); err == nil {
		t.Fatal("missing session_dek_ref must fail")
	}
	ref, err := ParsePrivateData(`{"session_dek_ref":"abc123"}`)
	if err != nil || ref != "abc123" {
		t.Fatalf("parse: %q %v", ref, err)
	}
}

// host_path containment: stage dir must stay inside volume_base_dir.
func TestStageDirContainment(t *testing.T) {
	if _, err := stageDir("relative/dir", "v"); err == nil {
		t.Fatal("relative base_dir must be rejected")
	}
	base := t.TempDir()
	d, err := stageDir(base, "vol-x")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(d, base+string(os.PathSeparator)) {
		t.Fatalf("stage dir %s escapes base %s", d, base)
	}
}

// Destroy with KMS revoke failure must RETAIN the volume tree.
func TestDestroyFailClosedOnRevokeFailure(t *testing.T) {
	p, m, _, done := testPlugin(t, "aes_fallback", "")
	defer done()
	ctx := context.Background()
	pd, _ := p.CreateVolume(ctx, "vol-r", "cds-data-r")
	_ = pd
	if _, _, err := p.PutVolumeData(ctx, "vol-r", "", []byte("x")); err != nil {
		t.Fatal(err)
	}
	// point the plugin at a dead KMS so revoke fails
	p.kms = NewKMSClient("http://127.0.0.1:1", "tok")
	if err := p.Destroy(ctx, "vol-r"); err == nil {
		t.Fatal("destroy with revoke failure must fail")
	}
	vdir := filepath.Join(p.cfg.VolumeRoot, "vol-r")
	if _, err := os.Stat(vdir); err != nil {
		t.Fatal("volume tree must be retained when DEK revoke fails")
	}
	// and the mock still holds the key (revocation never happened)
	if len(m.keys) == 0 {
		t.Fatal("KMS key must still exist")
	}
}

// cipher negative paths for both backends (tongsuo if helper present).
func TestCipherNegativePaths(t *testing.T) {
	helper := filepath.Join("..", "sm4gcm", "sm4_gcm")
	ciphers := map[string]Cipher{"aes_fallback": NewAESGCMCipher()}
	if _, err := os.Stat(helper); err == nil {
		ciphers["tongsuo"] = NewTongsuoCipher(helper, 30)
	}
	for name, c := range ciphers {
		t.Run(name, func(t *testing.T) {
			key, _ := randomBytes(dekLen)
			nonce, _ := randomBytes(nonceLen)
			aad := []byte("vol")
			pt := []byte("payload-123")
			ct, tag, err := c.Seal(key, nonce, aad, pt)
			if err != nil {
				t.Fatalf("seal: %v", err)
			}
			if bytes.Equal(ct, pt) {
				t.Fatal("ciphertext equals plaintext")
			}
			got, err := c.Open(key, nonce, aad, ct, tag)
			if err != nil || !bytes.Equal(got, pt) {
				t.Fatalf("open: %v", err)
			}
			bad := append([]byte(nil), ct...)
			bad[0] ^= 1
			if _, err := c.Open(key, nonce, aad, bad, tag); err == nil {
				t.Fatal("tampered ciphertext accepted")
			}
			if _, err := c.Open(key, nonce, []byte("vol2"), ct, tag); err == nil {
				t.Fatal("tampered aad accepted")
			}
		})
	}
}

// annotation payload parsing (spec §2.4 shape).
func TestParseAnnotation(t *testing.T) {
	payload := `[{"name":"cds-data-s1","driver":"cds-encrypted","private_data":"{\"session_dek_ref\":\"k1\"}"},{"name":"data","driver":"local","private_data":""}]`
	if err := parseAnnotationCmd(payload); err != nil {
		t.Fatal(err)
	}
	if err := parseAnnotationCmd(`[{"name":"x"}]`); err == nil {
		t.Fatal("entry without driver must fail")
	}
	if err := parseAnnotationCmd(`{"not":"array"}`); err == nil {
		t.Fatal("non-array must fail")
	}
}
