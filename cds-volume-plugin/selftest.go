package main

import (
	"bytes"
	"fmt"
)

// RunSelfTest executes the boot self-test (spec CB-03 step 1: encrypt /
// tag verify / decrypt round trip). A failure here must keep the plugin
// from serving — the caller exits non-zero. With the tongsuo backend the
// whole round trip runs through the sm4_gcm subprocess, so a self-test
// pass also proves the helper + libtongsuo wiring works.
func RunSelfTest(c Cipher) error {
	key, err := randomBytes(dekLen)
	if err != nil {
		return err
	}
	defer zeroize(key)
	nonce, err := randomBytes(nonceLen)
	if err != nil {
		return err
	}
	aad := []byte("cds-volume-plugin-selftest")
	pt := make([]byte, 4096)
	for i := range pt {
		pt[i] = byte(i * 7)
	}

	ct, tag, err := c.Seal(key, nonce, aad, pt)
	if err != nil {
		return fmt.Errorf("self-test seal: %w", err)
	}
	opened, err := c.Open(key, nonce, aad, ct, tag)
	if err != nil {
		return fmt.Errorf("self-test open: %w", err)
	}
	if !bytes.Equal(opened, pt) {
		return fmt.Errorf("self-test: round-trip mismatch")
	}

	// negative path: a tampered tag/ciphertext MUST be rejected (GCM tag check)
	bad := make([]byte, len(ct))
	copy(bad, ct)
	bad[0] ^= 1
	if _, err := c.Open(key, nonce, aad, bad, tag); err == nil {
		return fmt.Errorf("self-test: tampered ciphertext was ACCEPTED (critical)")
	}
	badtag := make([]byte, len(tag))
	copy(badtag, tag)
	badtag[0] ^= 1
	if _, err := c.Open(key, nonce, aad, ct, badtag); err == nil {
		return fmt.Errorf("self-test: tampered tag was ACCEPTED (critical)")
	}
	return nil
}
