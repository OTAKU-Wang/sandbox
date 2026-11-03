package vol

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"log"
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
	DekLen   = 16
	NonceLen = 12
	TagLen   = 16
)

// Helper budget bounds (A176). CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS is a
// millisecond knob; a budget under a second cannot be honoured by a subprocess
// spawn at all, so it is raised to the floor instead of silently becoming "0".
const (
	minHelperTimeout     = time.Second
	defaultHelperTimeout = 10 * time.Second
)

// CheckHelperBudget is the single source of truth for what the millisecond knob
// may say. Both boot paths call it — Config.Validate() (so a bad value is refused
// as a config error, exit 2) and NewCipher (so a hand-built Config cannot slip
// past it) — instead of each restating the threshold (A202③). Only a budget that
// cannot fire at all is refused here: a sub-second value stays legal and is
// raised to the floor by NewTongsuoCipherTimeout, with a WARNING.
func CheckHelperBudget(d time.Duration) error {
	if d <= 0 {
		return fmt.Errorf("helper budget %s cannot bound a subprocess: CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS "+
			"must be a positive number of milliseconds (fail-closed, refusing to boot)", d)
	}
	return nil
}

// TongsuoCipher drives the libtongsuo-linked sm4_gcm helper as a subprocess
// with a strict binary stdin/stdout protocol (see sm4gcm/sm4_gcm.h).
// Any non-zero helper exit is a hard error — there is no fallback path.
type TongsuoCipher struct {
	helperPath string
	timeout    time.Duration // budget actually applied to each helper call
	configured time.Duration // budget the operator configured, for honest errors
}

// NewTongsuoCipher keeps the historical whole-second signature (plugin_test.go,
// selftest). Production configuration goes through NewCipher, which must NOT
// funnel a millisecond value through it: int(Seconds()) used to turn 200ms into
// 0s and make every helper call time out instantly (A176).
func NewTongsuoCipher(helperPath string, timeoutSecs int) *TongsuoCipher {
	return NewTongsuoCipherTimeout(helperPath, time.Duration(timeoutSecs)*time.Second)
}

func NewTongsuoCipherTimeout(helperPath string, timeout time.Duration) *TongsuoCipher {
	effective := timeout
	if effective <= 0 {
		effective = defaultHelperTimeout
	} else if effective < minHelperTimeout {
		effective = minHelperTimeout
	}
	if effective != timeout {
		log.Printf("WARN: sm4 helper budget %s is not usable as configured; using %s "+
			"(CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS, fail-closed floor %s)", timeout, effective, minHelperTimeout)
	}
	return &TongsuoCipher{helperPath: helperPath, timeout: effective, configured: timeout}
}

// checkKeyMaterial is the fail-closed input gate of the Cipher interface (A177).
// The helper frame is fixed-offset (key(16)||nonce(12)||aadlen(4)||aad||[tag]||body),
// and Seal/Open fill the key slot with copy(), which moves min(len(key),16) bytes:
// without this gate a short key silently becomes a zero-padded key, a long key is
// silently truncated, and a wrong nonce/tag desynchronises the C helper's reads.
// It also pins both backends to the SM4 shape, so data sealed by the dev fallback
// is always openable by the production helper.
func checkKeyMaterial(op string, key, nonce []byte) error {
	if len(key) != DekLen {
		return fmt.Errorf("%s: key must be %d bytes, got %d (fail-closed)", op, DekLen, len(key))
	}
	if len(nonce) != NonceLen {
		return fmt.Errorf("%s: nonce must be %d bytes, got %d (fail-closed)", op, NonceLen, len(nonce))
	}
	return nil
}

func checkTagLength(op string, tag []byte) error {
	if len(tag) != TagLen {
		return fmt.Errorf("%s: tag must be %d bytes, got %d (fail-closed)", op, TagLen, len(tag))
	}
	return nil
}

func (c *TongsuoCipher) Name() string { return "sm4-gcm(tongsuo)" }

func (c *TongsuoCipher) Run(op string, stdin []byte) ([]byte, error) {
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
	case <-time.After(c.timeout):
		return nil, c.timeoutErr(op, cmd, done)
	}
}

// timeoutErr reports a helper that outlived its budget. The Kill error is part of
// the report, and waiting for the reaping goroutine is bounded: cmd.Wait() reaps
// the process and then blocks until every stdout/stderr copy finishes, so a
// descendant of the helper that still holds those pipes keeps `done` from ever
// firing — an unbounded <-done here would pin the calling gRPC handler goroutine
// for the life of that process, voiding the fail-closed timeout this branch
// exists to guarantee (A178).
func (c *TongsuoCipher) timeoutErr(op string, cmd *exec.Cmd, done <-chan error) error {
	killErr := cmd.Process.Kill()
	grace := 2 * c.timeout
	reaped := false
	select {
	case <-done:
		reaped = true
	case <-time.After(grace):
	}

	err := fmt.Errorf("sm4 helper %s: timeout after %s (configured %s, fail-closed)", op, c.timeout, c.configured)
	if killErr != nil {
		err = fmt.Errorf("%w; helper process could not be killed: %v", err, killErr)
	}
	if !reaped {
		err = fmt.Errorf("%w; helper process was not reaped within %s of Kill (a descendant still holds its pipes)", err, grace)
	}
	return err
}

func HelperFrame(nonce, aad, mid, body []byte) []byte {
	var buf []byte
	buf = append(buf, make([]byte, DekLen)...) // key slot filled by caller
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
	if err := checkKeyMaterial("sm4 seal", key, nonce); err != nil {
		return nil, nil, err
	}
	in := HelperFrame(nonce, aad, nil, plaintext)
	copy(in, key)
	out, err := c.Run("encrypt", in)
	if err != nil {
		return nil, nil, err
	}
	if len(out) < TagLen+len(plaintext) {
		return nil, nil, fmt.Errorf("sm4 helper: short output %d (fail-closed)", len(out))
	}
	return out[TagLen : TagLen+len(plaintext)], out[:TagLen], nil
}

func (c *TongsuoCipher) Open(key, nonce, aad, ciphertext, tag []byte) ([]byte, error) {
	if err := checkKeyMaterial("sm4 open", key, nonce); err != nil {
		return nil, err
	}
	if err := checkTagLength("sm4 open", tag); err != nil {
		return nil, err
	}
	in := HelperFrame(nonce, aad, tag, ciphertext)
	copy(in, key)
	out, err := c.Run("decrypt", in)
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

func (c *AESGCMCipher) GCM(key, nonce []byte) (cipher.AEAD, error) {
	b, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	// NewGCM, not NewGCMWithNonceSize: the SM4 helper only ever reads a 12-byte
	// nonce, so accepting another size here would seal data the production
	// backend can never open (A177).
	return cipher.NewGCM(b)
}

func (c *AESGCMCipher) Seal(key, nonce, aad, plaintext []byte) ([]byte, []byte, error) {
	if err := checkKeyMaterial("aes seal", key, nonce); err != nil {
		return nil, nil, err
	}
	g, err := c.GCM(key, nonce)
	if err != nil {
		return nil, nil, err
	}
	out := g.Seal(nil, nonce, plaintext, aad)
	return out[:len(plaintext):len(plaintext)], out[len(plaintext):], nil
}

func (c *AESGCMCipher) Open(key, nonce, aad, ciphertext, tag []byte) ([]byte, error) {
	if err := checkKeyMaterial("aes open", key, nonce); err != nil {
		return nil, err
	}
	if err := checkTagLength("aes open", tag); err != nil {
		return nil, err
	}
	g, err := c.GCM(key, nonce)
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
		if err := CheckHelperBudget(cfg.SubprocTimeout); err != nil {
			return nil, err
		}
		return NewTongsuoCipherTimeout(cfg.HelperPath, cfg.SubprocTimeout), nil
	case "aes_fallback":
		return NewAESGCMCipher(), nil
	default:
		return nil, fmt.Errorf("unknown backend %q", cfg.Backend)
	}
}

func RandomBytes(n int) ([]byte, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return nil, err
	}
	return b, nil
}
