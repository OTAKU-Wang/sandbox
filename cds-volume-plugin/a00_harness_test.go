package main

// go01 shared test harness. Self-contained on purpose: the package-level
// helpers in plugin_test.go belong to other tracks and may change shape
// mid-round, so every track-go-01 test builds its own plugin + KMS stand-in.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

type go01KMS struct {
	mu           sync.Mutex
	Refs         []string // refs handed out by register, in order
	Revoked      []string // refs DELETEd, in order
	Requests     []string // "METHOD path"
	Keys         map[string]string
	RevokeStatus int    // 0 → 204 (contract)
	RevokeBody   string // body served with RevokeStatus
	FailRevoke   bool
}

func (k *go01KMS) snapshot(fn func(k *go01KMS)) {
	k.mu.Lock()
	defer k.mu.Unlock()
	fn(k)
}

func (k *go01KMS) revokedCount() int {
	n := 0
	k.snapshot(func(kk *go01KMS) { n = len(kk.Revoked) })
	return n
}

func (k *go01KMS) lastPath() string {
	p := ""
	k.snapshot(func(kk *go01KMS) {
		if len(kk.Requests) > 0 {
			p = kk.Requests[len(kk.Requests)-1]
		}
	})
	return p
}

func (k *go01KMS) requestCount() int {
	n := 0
	k.snapshot(func(kk *go01KMS) { n = len(kk.Requests) })
	return n
}

// go01MockKMS serves the three volume-key routes the plugin consumes, with the
// same status codes as app/api/kms.py (201 register, 200 distribute, 204 revoke).
func go01MockKMS(t *testing.T) (*go01KMS, *httptest.Server) {
	t.Helper()
	k := &go01KMS{Keys: map[string]string{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body []byte
		if r.Body != nil {
			body, _ = readAllOf(r)
		}
		k.mu.Lock()
		k.Requests = append(k.Requests, r.Method+" "+r.URL.Path)
		defer k.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/kms/volume-keys":
			var req struct {
				DEKHex string `json:"dek_hex"`
			}
			_ = json.Unmarshal(body, &req)
			ref := "vkey-" + strings.Repeat("0", 32-len(k.Refs)) + itoaGo01(len(k.Refs)+1)
			k.Refs = append(k.Refs, ref)
			k.Keys[ref] = req.DEKHex
			_ = json.NewEncoder(w).Encode(map[string]string{"key_id": ref})
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/distribute"):
			ref := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/v1/kms/volume-keys/"), "/distribute")
			dek, ok := k.Keys[ref]
			if !ok {
				http.Error(w, "unknown ref", http.StatusNotFound)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]string{"key_hex": dek})
		case r.Method == http.MethodDelete && strings.HasPrefix(r.URL.Path, "/api/v1/kms/volume-keys/"):
			ref := strings.TrimPrefix(r.URL.Path, "/api/v1/kms/volume-keys/")
			if k.FailRevoke || k.RevokeStatus >= 400 {
				st := k.RevokeStatus
				if st == 0 {
					st = http.StatusInternalServerError
				}
				w.WriteHeader(st)
				_, _ = w.Write([]byte(k.RevokeBody))
				return
			}
			k.Revoked = append(k.Revoked, ref)
			st := k.RevokeStatus
			if st == 0 {
				st = http.StatusNoContent
			}
			w.WriteHeader(st)
			_, _ = w.Write([]byte(k.RevokeBody))
		default:
			http.Error(w, "unexpected", http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return k, srv
}

func readAllOf(r *http.Request) ([]byte, error) {
	buf := make([]byte, 0, 512)
	tmp := make([]byte, 512)
	for {
		n, err := r.Body.Read(tmp)
		buf = append(buf, tmp[:n]...)
		if err != nil {
			return buf, nil
		}
	}
}

func itoaGo01(n int) string {
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

// go01Plugin wires a plugin on the dev AES-GCM fallback (the only backend
// runnable without libtongsuo) against the stand-in KMS and a temp volume root.
func go01Plugin(t *testing.T, root string, k *go01KMS, srv *httptest.Server) *Plugin {
	t.Helper()
	c := NewAESGCMCipher()
	if err := RunSelfTest(c); err != nil {
		t.Fatalf("self-test: %v", err)
	}
	kms, err := NewKMSClient(srv.URL, "test-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}
	return NewPlugin(Config{VolumeRoot: root, Backend: "aes_fallback", MaxVolumeBytes: 1 << 30}, c, kms)
}

// go01PrivateData is the annotation payload CDS hands the plugin (spec §2.4).
func go01PrivateData(ref string) string {
	b, _ := json.Marshal(map[string]string{"session_dek_ref": ref})
	return string(b)
}
