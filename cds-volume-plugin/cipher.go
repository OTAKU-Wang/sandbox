package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"os/exec"
	"time"
)

// Cipher is the at-rest AEAD interface. Keys are 128-bit (SM4 key size).
// Open must return an error on ANY tag/ciphertext/aad mismatch and must not
// return unauthenticated plaintext (fail-closed, 禁伪造 red line).
type Cipher interface {
	Name() string
	Seal(key, nonce, aad, plaintext []byte) (ciphertext, tag []byte, err error)
	Open(key, nonce, aad, ciphertext, tag []byte) (plaintext []byte, err error)
}

const (
	dekLen   = 16
	nonceLen = 12
	tagLen   = 16
)

// TongsuoCipher drives the libtongsuo-linked sm4_gcm helper as a subprocess
// with a strict binary stdin/stdout protocol (see sm4gcm/sm4_gcm.h).
// Any non-zero helper exit is a hard error — there is no fallback path.
type TongsuoCipher struct {
	helperPath string
	timeout    int // seconds
}

func NewTongsuoCipher(helperPath string, timeoutSecs int) *TongsuoCipher {
	return &TongsuoCipher{helperPath: helperPath, timeout: timeoutSecs}
}

func (c *TongsuoCipher) Name() string { return "sm4-gcm(tongsuo)" }

func (c *TongsuoCipher) run(op string, stdin []byte) ([]byte, error) {
	cmd := exec.Command(c.helperPath, op)
	cmd.Stdin = bytes.NewReader(stdin)
	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("sm4 helper start: %w", err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		if err != nil {
			return nil, fmt.Errorf("sm4 helper %s: %v: %s", op, err, errBuf.String())
		}
		return outBuf.Bytes(), nil
	case <-time.After(time.Duration(c.timeout) * time.Second):
		_ = cmd.Process.Kill()
		<-done
		return nil, fmt.Errorf("sm4 helper %s: timeout after %ds (fail-closed)", op, c.timeout)
	}
}

func helperFrame(nonce, aad, mid, body []byte) []byte {
	var buf []byte
	buf = append(buf, make([]byte, dekLen)...) // key slot filled by caller
	buf = append(buf, nonce...)
	var lenB [4]byte
	binary.BigEndian.PutUint32(lenB[:], uint32(len(aad)))
	buf = append(buf, lenB[:]...)
	buf = append(buf, aad...)
	buf = append(buf, mid...) // tag (decrypt) or empty (encrypt)
	buf = append(buf, body...)
	return buf
}

func (c *TongsuoCipher) Seal(key, nonce, aad, plaintext []byte) ([]byte, []byte, error) {
	in := helperFrame(nonce, aad, nil, plaintext)
	copy(in, key)
	out, err := c.run("encrypt", in)
	if err != nil {
		return nil, nil, err
	}
	if len(out) < tagLen+len(plaintext) {
		return nil, nil, fmt.Errorf("sm4 helper: short output %d (fail-closed)", len(out))
	}
	return out[tagLen : tagLen+len(plaintext)], out[:tagLen], nil
}

func (c *TongsuoCipher) Open(key, nonce, aad, ciphertext, tag []byte) ([]byte, error) {
	in := helperFrame(nonce, aad, tag, ciphertext)
	copy(in, key)
	out, err := c.run("decrypt", in)
	if err != nil {
		return nil, err // tag/ciphertext/aad mismatch surfaces as non-zero exit
	}
	if len(out) != len(ciphertext) {
		return nil, fmt.Errorf("sm4 helper: unexpected output length %d (fail-closed)", len(out))
	}
	return out, nil
}

// AESGCMCipher is the dev/test fallback (M-01: not GM). It is a real AEAD
// cipher; the fallback is disclosed at boot and never used when
// CDS_SM4_AEAD_BACKEND=tongsuo.
type AESGCMCipher struct{}

func NewAESGCMCipher() *AESGCMCipher { return &AESGCMCipher{} }

func (c *AESGCMCipher) Name() string { return "aes-128-gcm(fallback,non-GM)" }

func (c *AESGCMCipher) gcm(key, nonce []byte) (cipher.AEAD, error) {
	b, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCMWithNonceSize(b, len(nonce))
}

func (c *AESGCMCipher) Seal(key, nonce, aad, plaintext []byte) ([]byte, []byte, error) {
	g, err := c.gcm(key, nonce)
	if err != nil {
		return nil, nil, err
	}
	out := g.Seal(nil, nonce, plaintext, aad)
	return out[:len(plaintext):len(plaintext)], out[len(plaintext):], nil
}

func (c *AESGCMCipher) Open(key, nonce, aad, ciphertext, tag []byte) ([]byte, error) {
	g, err := c.gcm(key, nonce)
	if err != nil {
		return nil, err
	}
	ct := make([]byte, 0, len(ciphertext)+len(tag))
	ct = append(ct, ciphertext...)
	ct = append(ct, tag...)
	pt, err := g.Open(nil, nonce, ct, aad)
	if err != nil {
		return nil, fmt.Errorf("aes-gcm open: %w (auth mismatch — fail-closed)", errors.New("auth failed"))
	}
	return pt, nil
}

// NewCipher constructs the configured backend.
func NewCipher(cfg Config) (Cipher, error) {
	switch cfg.Backend {
	case "tongsuo":
		return NewTongsuoCipher(cfg.HelperPath, int(cfg.SubprocTimeout.Seconds())), nil
	case "aes_fallback":
		return NewAESGCMCipher(), nil
	default:
		return nil, fmt.Errorf("unknown backend %q", cfg.Backend)
	}
}

func randomBytes(n int) ([]byte, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return nil, err
	}
	return b, nil
}
