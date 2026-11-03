package vol_test

import . "cds-volume-plugin/internal/vol"

// A177 (OCR 2026-10-18, medium) — the Cipher interface never checked its inputs.
// HelperFrame() opens a 16-byte ZERO key slot and Seal/Open fill it with
// copy(in, key), which moves min(len(key),16) bytes: a short key silently
// became a zero-padded key, a long key silently truncated, and nonce/tag lengths
// were unchecked although the C helper reads them at fixed offsets
// (sm4gcm/sm4_gcm.c: RD_FULL(key); RD_FULL(nonce)). The AES fallback accepted
// ANY nonce size (NewGCMWithNonceSize) and AES-192/256 keys, so the dev backend
// could "work" with material the production backend can never decrypt.
// These are the fail-closed / 禁伪造 boundary of the key path.

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// g02aMarkingHelper is a helper stand-in that leaves a mark file when invoked,
// so a test can prove a malformed frame is refused BEFORE the helper is run
// (a key of the wrong width must never reach the cipher engine at all).
func g02aMarkingHelper(t *testing.T) (string, string) {
	t.Helper()
	dir := t.TempDir()
	helper := filepath.Join(dir, "fake-sm4-gcm")
	mark := filepath.Join(dir, "invoked")
	script := fmt.Sprintf("#!/bin/sh\ntouch '%s'\ncat >/dev/null\ndd if=/dev/zero bs=32 count=1 2>/dev/null\n", mark)
	if err := os.WriteFile(helper, []byte(script), 0o700); err != nil {
		t.Fatalf("write helper stand-in: %v", err)
	}
	return helper, mark
}

func g02aInvoked(mark string) bool {
	_, err := os.Stat(mark)
	return err == nil
}

type g02aCase struct {
	Name            string
	Key, Nonce, Tag []byte
	Open            bool
	WantMention     string
}

// g02aRun drives one malformed-input case. `forget` is called between the Seal and
// the Open of an open-case so the "helper never ran" assertion describes the
// refusal under test, not the legitimate Seal that produced the ciphertext.
func g02aRun(t *testing.T, c Cipher, tc g02aCase, forget func()) error {
	t.Helper()
	ct, _, err := c.Seal(tc.Key, tc.Nonce, []byte("aad"), g02aPlain)
	if tc.Open {
		if err != nil {
			return err
		}
		forget()
		_, err = c.Open(tc.Key, tc.Nonce, []byte("aad"), ct, tc.Tag)
		return err
	}
	return err
}

// TestA177_MalformedKeyMaterialRefusedOnBothBackends: one interface, one input
// contract. The tongsuo cases must be rejected without invoking the helper.
func TestA177_MalformedKeyMaterialRefusedOnBothBackends(t *testing.T) {
	type backend struct {
		Name string
		make func(t *testing.T) (Cipher, func() bool, func())
	}
	backends := []backend{
		{"tongsuo", func(t *testing.T) (Cipher, func() bool, func()) {
			helper, mark := g02aMarkingHelper(t)
			c, err := g02aTongsuo(t, helper, 2*time.Second)
			if err != nil {
				t.Fatalf("NewCipher(tongsuo): %v", err)
			}
			return c, func() bool { return g02aInvoked(mark) }, func() { _ = os.Remove(mark) }
		}},
		{"aes_fallback", func(t *testing.T) (Cipher, func() bool, func()) {
			c, err := NewCipher(Config{Backend: "aes_fallback", MaxVolumeBytes: 1 << 30})
			if err != nil {
				t.Fatalf("NewCipher(aes_fallback): %v", err)
			}
			return c, func() bool { return false }, func() {}
		}},
	}

	for _, tc := range []g02aCase{
		{Name: "key short", Key: bytes.Repeat([]byte{1}, DekLen-1), Nonce: g02aNonce, WantMention: "key"},
		{Name: "key long", Key: bytes.Repeat([]byte{1}, DekLen+1), Nonce: g02aNonce, WantMention: "key"},
		{Name: "key empty", Key: nil, Nonce: g02aNonce, WantMention: "key"},
		{Name: "nonce short", Key: g02aKey, Nonce: bytes.Repeat([]byte{2}, NonceLen-1), WantMention: "nonce"},
		{Name: "nonce long", Key: g02aKey, Nonce: bytes.Repeat([]byte{2}, NonceLen+4), WantMention: "nonce"},
		{Name: "tag short", Key: g02aKey, Nonce: g02aNonce, Tag: bytes.Repeat([]byte{3}, TagLen-1), Open: true, WantMention: "tag"},
	} {
		for _, b := range backends {
			t.Run(tc.Name+"/"+b.Name, func(t *testing.T) {
				cipher, invoked, forget := b.make(t)
				err := g02aRun(t, cipher, tc, forget)
				if err == nil {
					t.Fatalf("%s accepted %s (len(key)=%d len(nonce)=%d len(tag)=%d) with no error: a short key "+
						"is silently zero-padded and a long key silently truncated (A177)",
						cipher.Name(), tc.Name, len(tc.Key), len(tc.Nonce), len(tc.Tag))
				}
				if !strings.Contains(strings.ToLower(err.Error()), tc.WantMention) {
					t.Fatalf("%s rejection must name the offending input %q, got: %v", cipher.Name(), tc.WantMention, err)
				}
				if !strings.Contains(strings.ToLower(err.Error()), "fail-closed") {
					t.Fatalf("%s input rejection must be marked fail-closed like every other refusal, got: %v", cipher.Name(), err)
				}
				if invoked() {
					t.Fatalf("%s: malformed key material reached the helper subprocess; the refusal must happen "+
						"before the frame is ever written to it (A177)", cipher.Name())
				}
			})
		}
	}
}

// TestA177_BackendsAgreeOnKeyAndNonceShape: the cross-backend trap. AES-192 and a
// 8-byte nonce are valid AEAD parameters in Go but impossible for the SM4 helper,
// so accepting them on the dev backend produces data production cannot decrypt.
func TestA177_BackendsAgreeOnKeyAndNonceShape(t *testing.T) {
	aes, err := NewCipher(Config{Backend: "aes_fallback", MaxVolumeBytes: 1 << 30})
	if err != nil {
		t.Fatalf("NewCipher(aes_fallback): %v", err)
	}
	oddKey := bytes.Repeat([]byte{7}, 24)  // AES-192: accepted by crypto/aes, impossible for SM4
	oddNonce := bytes.Repeat([]byte{9}, 8) // accepted by NewGCMWithNonceSize, refused by the C helper

	if _, _, err := aes.Seal(oddKey, g02aNonce, nil, g02aPlain); err == nil {
		t.Fatal("aes fallback accepted a 24-byte key while the tongsuo backend can only take 16: " +
			"the same call succeeds in dev and fails in prod (A177)")
	}
	if _, _, err := aes.Seal(g02aKey, oddNonce, nil, g02aPlain); err == nil {
		t.Fatal("aes fallback accepted an 8-byte nonce via NewGCMWithNonceSize while the SM4 helper only " +
			"reads a fixed 12-byte nonce: cross-backend undecryptable data (A177)")
	}
}

// TestA177_ValidKeyMaterialStillWorks: the over-tightening guard — the shapes the
// KMS path actually produces must still seal and open.
func TestA177_ValidKeyMaterialStillWorks(t *testing.T) {
	aes, err := NewCipher(Config{Backend: "aes_fallback", MaxVolumeBytes: 1 << 30})
	if err != nil {
		t.Fatalf("NewCipher(aes_fallback): %v", err)
	}
	nonce, nerr := RandomBytes(NonceLen)
	if nerr != nil {
		t.Fatal(nerr)
	}
	key, kerr := RandomBytes(DekLen)
	if kerr != nil {
		t.Fatal(kerr)
	}
	ct, tag, serr := aes.Seal(key, nonce, []byte("vol-1"), g02aPlain)
	if serr != nil {
		t.Fatalf("Seal with conforming key/nonce: %v", serr)
	}
	back, oerr := aes.Open(key, nonce, []byte("vol-1"), ct, tag)
	if oerr != nil {
		t.Fatalf("Open of a fresh seal: %v", oerr)
	}
	if !bytes.Equal(back, g02aPlain) {
		t.Fatalf("round trip changed the plaintext: %q", back)
	}
}
