package main

import (
	"bytes"
	"fmt"
)

// RunSelfTest proves the configured Cipher really is an authenticated encryption
// with the properties the data path relies on (spec CB-03 step 1 / §2.9). It runs
// on startup and on the CLI, and any failure must abort boot — the 禁伪造 red line
// means a crypto backend is never trusted on its own word:
//
//   - Seal must transform the data, not merely re-emit it, or at-rest "ciphertext"
//     is plaintext while every check below still passes;
//   - Open must reject tampered ciphertext, tampered tag, a tampered AAD (the
//     volume label is what stops ciphertext being swapped between volumes) and a
//     foreign key;
//   - the tag must be a real authentication tag, and the failure lines must
//     disclose the broken property only — never the key, the AAD or the plaintext.
func RunSelfTest(c Cipher) error {
	key, err := randomBytes(dekLen)
	if err != nil {
		return fmt.Errorf("self-test key: %w", err)
	}
	defer zeroize(key)
	otherKey, err := randomBytes(dekLen)
	if err != nil {
		return fmt.Errorf("self-test key: %w", err)
	}
	defer zeroize(otherKey)
	nonce, err := randomBytes(nonceLen)
	if err != nil {
		return fmt.Errorf("self-test nonce: %w", err)
	}
	defer zeroize(nonce)

	aad := []byte("cds-volume-plugin-selftest")
	pt := make([]byte, 4096)
	for i := range pt {
		pt[i] = byte(i * 7)
	}
	defer zeroize(pt)

	ct, tag, err := c.Seal(key, nonce, aad, pt)
	if err != nil {
		return fmt.Errorf("self-test seal: %w", err)
	}
	defer zeroize(ct)

	if len(tag) != tagLen {
		return fmt.Errorf("self-test: AEAD tag is %d bytes, want %d — the backend does not authenticate", len(tag), tagLen)
	}
	if len(ct) != len(pt) {
		return fmt.Errorf("self-test: ciphertext is %d bytes for %d plaintext bytes — an AEAD backend must preserve length", len(ct), len(pt))
	}
	if bytes.Equal(ct, pt) || bytes.Contains(ct, pt[:64]) {
		return fmt.Errorf("self-test: Seal returned the plaintext as ciphertext — no encryption happened (fail-closed)")
	}

	opened, err := c.Open(key, nonce, aad, ct, tag)
	if err != nil {
		return fmt.Errorf("self-test open: %w", err)
	}
	defer zeroize(opened)
	if !bytes.Equal(opened, pt) {
		return fmt.Errorf("self-test: round-trip mismatch")
	}

	bad := append([]byte(nil), ct...)
	bad[0] ^= 0x01
	if out, err := c.Open(key, nonce, aad, bad, tag); err == nil {
		zeroize(out)
		return fmt.Errorf("self-test: tampered ciphertext was ACCEPTED (fail-closed violation)")
	}
	badTag := append([]byte(nil), tag...)
	badTag[0] ^= 0x01
	if out, err := c.Open(key, nonce, aad, ct, badTag); err == nil {
		zeroize(out)
		return fmt.Errorf("self-test: tampered tag was ACCEPTED (fail-closed violation)")
	}
	badAAD := append([]byte(nil), aad...)
	badAAD[0] ^= 0x01
	if out, err := c.Open(key, nonce, badAAD, ct, tag); err == nil {
		zeroize(out)
		return fmt.Errorf("self-test: ciphertext opened under a tampered AAD — the backend does not bind the volume label (fail-closed violation)")
	}
	if out, err := c.Open(otherKey, nonce, aad, ct, tag); err == nil {
		zeroize(out)
		return fmt.Errorf("self-test: ciphertext opened under a foreign key (fail-closed violation)")
	}
	return nil
}
