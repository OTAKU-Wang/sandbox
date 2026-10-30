package main

// A202 (OCR 2026-10-18, medium) sub-item 1 — the idempotency comparison in
// PutVolumeData decrypts the volume into `existing` and never zeroizes it, while
// Attach clears the same kind of buffer on all three of its exits.

import (
	"context"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

type a202Spy struct {
	mu      sync.Mutex
	handed  [][]byte
	content [][]byte
}

func (s *a202Spy) Name() string { return "aes-128-gcm(fallback,non-GM)" }

func (s *a202Spy) Seal(key, nonce, aad, plaintext []byte) ([]byte, []byte, error) {
	return NewAESGCMCipher().Seal(key, nonce, aad, plaintext)
}

func (s *a202Spy) Open(key, nonce, aad, ciphertext, tag []byte) ([]byte, error) {
	plain, err := NewAESGCMCipher().Open(key, nonce, aad, ciphertext, tag)
	if err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.handed = append(s.handed, plain)
	s.content = append(s.content, append([]byte(nil), plain...))
	return plain, nil
}

func (s *a202Spy) report(t *testing.T, wantOpens int) {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.handed) < wantOpens {
		t.Fatalf("the spy saw %d Open() call(s), want at least %d — the path under test never decrypted, so it proved nothing",
			len(s.handed), wantOpens)
	}
	for i, buf := range s.handed {
		allZero := true
		for _, b := range buf {
			if b != 0 {
				allZero = false
				break
			}
		}
		if allZero {
			continue
		}
		if !containsPlaintext(s.content[i]) {
			t.Fatalf("spy bookkeeping is wrong for buffer %d", i)
		}
		t.Fatalf("plaintext buffer %d (%d bytes) handed out by Cipher.Open is still readable after "+
			"PutVolumeData returned: it held %q", i, len(buf), s.content[i])
	}
}

func containsPlaintext(b []byte) bool {
	for _, x := range b {
		if x != 0 {
			return true
		}
	}
	return false
}

func a202Plugin(t *testing.T, root string, srv_url string, spy *a202Spy) *Plugin {
	t.Helper()
	kms, err := NewKMSClient(srv_url, "test-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}
	return NewPlugin(Config{VolumeRoot: root, Backend: "aes_fallback", MaxVolumeBytes: 1 << 30}, spy, kms)
}

func TestA202_IdempotencyComparisonZeroizesPlaintext(t *testing.T) {
	_, srv := go01MockKMS(t)
	root := filepath.Join(t.TempDir(), "volumes")
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatalf("volume root: %v", err)
	}
	spy := &a202Spy{}
	p := a202Plugin(t, root, srv.URL, spy)
	ctx := context.Background()
	const id = "vol-1"
	data := []byte("secret rows that must not linger in the heap\n")

	if _, err := p.CreateVolume(ctx, id, "cds-data-"+id); err != nil {
		t.Fatalf("create: %v", err)
	}
	if _, _, err := p.PutVolumeData(ctx, id, "", data); err != nil {
		t.Fatalf("first put: %v", err)
	}
	spy.report(t, 0) // the first put seals, never opens: no buffer to check yet

	if _, idem, err := p.PutVolumeData(ctx, id, "", data); err != nil || !idem {
		t.Fatalf("idempotent retry = (_, %v, %v), want idempotent success", idem, err)
	}
	spy.report(t, 1)
}

func TestA202_MismatchedRetryAlsoZeroizesPlaintext(t *testing.T) {
	_, srv := go01MockKMS(t)
	root := filepath.Join(t.TempDir(), "volumes")
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatalf("volume root: %v", err)
	}
	spy := &a202Spy{}
	p := a202Plugin(t, root, srv.URL, spy)
	ctx := context.Background()
	const id = "vol-1"

	if _, err := p.CreateVolume(ctx, id, "cds-data-"+id); err != nil {
		t.Fatalf("create: %v", err)
	}
	if _, _, err := p.PutVolumeData(ctx, id, "", []byte("original payload\n")); err != nil {
		t.Fatalf("first put: %v", err)
	}
	if _, _, err := p.PutVolumeData(ctx, id, "", []byte("different payload\n")); err == nil {
		t.Fatalf("a non-identical retry was accepted")
	}
	spy.report(t, 1)
}
