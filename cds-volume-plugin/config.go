package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// EnvAllowNonRoot is the explicit operator opt-in that lets the plugin serve
// while it is NOT root. Without it, Serve() refuses: a non-root plugin cannot own
// its socket, cannot chown staged plaintext and cannot loop-mount a sandbox rootfs
// (A205① — those failures used to be WARN lines next to a healthy-looking boot).
const EnvAllowNonRoot = "CDS_VOLUME_PLUGIN_ALLOW_NON_ROOT"

// EnvKMSToken names the service credential the control plane authenticates with.
const EnvKMSToken = "CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN"

// Config is the plugin's environment configuration (SS-10 CB-03).
// Key matrix: spec §6.1 (CDS_CUBE_VOLUME_PLUGIN_SOCKET, CDS_SM4_AEAD_BACKEND)
// plus CDS_VOLUME_PLUGIN_* plugin-local keys.
type Config struct {
	// SocketPath is the unix domain socket the plugin serves (spec §6.1).
	SocketPath string
	// VolumeRoot is the at-rest ciphertext directory tree.
	VolumeRoot string
	// Backend selects the AEAD backend: "tongsuo" (libtongsuo helper, GM)
	// or "aes_fallback" (Go AES-128-GCM, dev/test only, loud warning).
	// Spec §6.1 default is aes_fallback; production MUST run tongsuo.
	Backend string
	// HelperPath is the sm4_gcm helper binary (tongsuo backend).
	HelperPath string
	// SubprocTimeout bounds every helper invocation (spec §2.9 subprocess timeout).
	SubprocTimeout time.Duration
	// KMSBaseURL is the CDS control-plane base URL for DEK registration.
	// A non-loopback URL must be https (A179); see kms.go validateKMSEndpoint.
	KMSBaseURL string
	// KMSToken is the service credential presented to the control plane.
	KMSToken string
	// KMSCAFile pins the control-plane CA bundle (PEM) for the KMS transport.
	KMSCAFile string
	// KMSClientCertFile/KMSClientKeyFile are this node's mTLS keypair toward
	// the control plane (both or neither).
	KMSClientCertFile string
	KMSClientKeyFile  string
	// KMSAllowInsecure knowingly accepts cleartext http to a NON-loopback
	// control plane. Operator opt-in only; always paired with a boot WARNING.
	KMSAllowInsecure bool
	// KMSInsecureSkipVerify disables KMS server-certificate verification.
	// Test networks only; never implied by any other setting.
	KMSInsecureSkipVerify bool
	// AllowNonRoot knowingly serves without root privileges (see EnvAllowNonRoot).
	AllowNonRoot bool
	// NodeID identifies this node in logs/audit.
	NodeID string
	// MaxVolumeBytes bounds a single volume data blob.
	MaxVolumeBytes int64
}

func envStr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt64(key string, def int64) int64 {
	if v := os.Getenv(key); v != "" {
		n, err := strconv.ParseInt(v, 10, 64)
		if err == nil {
			return n
		}
	}
	return def
}

// envBool reads an operator switch: only 1/true/on/yes switches anything on, so
// a typo can never enable a security downgrade by accident.
func envBool(key string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "1", "true", "on", "yes":
		return true
	default:
		return false
	}
}

// LoadConfig reads CDS_* environment configuration.
func LoadConfig() Config {
	return Config{
		SocketPath:     envStr("CDS_CUBE_VOLUME_PLUGIN_SOCKET", "/run/cds-volume-plugin/cds.sock"),
		VolumeRoot:     envStr("CDS_VOLUME_PLUGIN_VOLUME_ROOT", "/var/lib/cds/volumes"),
		Backend:        envStr("CDS_SM4_AEAD_BACKEND", "aes_fallback"),
		HelperPath:     envStr("CDS_VOLUME_PLUGIN_SM4_HELPER", "sm4_gcm"),
		SubprocTimeout: time.Duration(envInt64("CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS", 10000)) * time.Millisecond,
		KMSBaseURL:     envStr("CDS_VOLUME_PLUGIN_KMS_BASE_URL", "http://127.0.0.1:8000"),
		KMSToken:       envStr(EnvKMSToken, ""),

		KMSCAFile:             envStr("CDS_VOLUME_PLUGIN_KMS_CA_FILE", ""),
		KMSClientCertFile:     envStr("CDS_VOLUME_PLUGIN_KMS_CLIENT_CERT", ""),
		KMSClientKeyFile:      envStr("CDS_VOLUME_PLUGIN_KMS_CLIENT_KEY", ""),
		KMSAllowInsecure:      envBool("CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE"),
		KMSInsecureSkipVerify: envBool("CDS_VOLUME_PLUGIN_KMS_INSECURE_SKIP_VERIFY"),
		AllowNonRoot:          envBool(EnvAllowNonRoot),
		NodeID:                envStr("CDS_VOLUME_PLUGIN_NODE_ID", "local"),
		MaxVolumeBytes:        envInt64("CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES", 1<<30),
	}
}

func (c Config) Validate() error {
	if c.VolumeRoot == "" || c.VolumeRoot[0] != '/' {
		return fmt.Errorf("volume root must be absolute: %q", c.VolumeRoot)
	}
	switch c.Backend {
	case "tongsuo", "aes_fallback":
	default:
		return fmt.Errorf("unknown CDS_SM4_AEAD_BACKEND %q (want tongsuo|aes_fallback)", c.Backend)
	}
	if c.MaxVolumeBytes <= 0 {
		return fmt.Errorf("max volume bytes must be positive")
	}
	if c.Backend == "tongsuo" {
		// Same canonical predicate the cipher backend uses (cipher.go), so the two
		// cannot drift: an unusable helper budget is a config defect and must be
		// refused here, before the KMS round trip and before it can surface as a
		// crypto/self-test failure that blames the helper (A202③).
		if err := checkHelperBudget(c.SubprocTimeout); err != nil {
			return err
		}
		if err := validateGMHelperPath(c.HelperPath); err != nil {
			return err
		}
	}
	if strings.TrimSpace(c.KMSToken) == "" {
		// The control plane's own gate answers 401 to an unauthenticated caller
		// (app/core/deps.py require_kms_service_token), so booting without the
		// credential produces a plugin that serves and then fails at the FIRST DEK
		// call. Refuse here, where the message can name the knob (A205③).
		return fmt.Errorf("%s is not set: the plugin would serve and then get 401 on the first "+
			"RegisterDEK/UnwrapDEK, because the KMS authenticates the bearer token",
			EnvKMSToken)
	}
	if err := c.checkTrustRoots(); err != nil {
		return err
	}
	return nil
}

// validateGMHelperPath is the A175 boot gate for the tongsuo helper binary. The
// helper is fed the plaintext DEK on stdin, so WHICH binary it is must never be
// a PATH-resolution decision and must never rest in actor-writable storage:
// a relative name is resolved by exec against PATH (a node's PATH routinely
// contains user-writable directories), and a helper owned by a non-root uid or
// carrying a group/world write bit can be swapped for a collector at any time.
// Every refusal below is fail-closed — boot stops, it is never a WARNING.
//
// Boundary (locked by a202_config_test.go and TestA175B_MissingAbsoluteHelper…):
// an absolute-but-ABSENT helper stays config-legal. exec never searches PATH
// for an absolute path, so cipher.go already fails every Seal/Open closed with
// "helper start: … not found", and image presence is pinned by the build-time
// `test -x` (Dockerfile, A175C). Refusing absence here would change what boots.
func validateGMHelperPath(path string) error {
	if !filepath.IsAbs(path) {
		return fmt.Errorf("CDS_VOLUME_PLUGIN_SM4_HELPER must be an absolute path, got %q: a relative name is resolved against PATH, and the helper receives the plaintext DEK on stdin — a user-writable PATH directory on the node turns every Seal/Open into a DEK handover (fail-closed, A175)", path)
	}
	fi, err := os.Stat(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("stat CDS_VOLUME_PLUGIN_SM4_HELPER %s: %w (fail-closed: ownership cannot be established, A175)", path, err)
	}
	if fi.IsDir() {
		return fmt.Errorf("CDS_VOLUME_PLUGIN_SM4_HELPER %s is a directory (fail-closed, A175)", path)
	}
	uid, writableByOthers, ok := helperOwnership(fi)
	if !ok {
		return fmt.Errorf("cannot determine the owning uid of CDS_VOLUME_PLUGIN_SM4_HELPER %s on this platform (fail-closed, A175)", path)
	}
	if uid != 0 {
		return fmt.Errorf("CDS_VOLUME_PLUGIN_SM4_HELPER %s is owned by uid %d, not root: whoever owns the file can replace the binary that is fed the plaintext DEK (fail-closed, A175)", path, uid)
	}
	if writableByOthers {
		return fmt.Errorf("CDS_VOLUME_PLUGIN_SM4_HELPER %s carries a group/world write bit (perm %04o): the root-owner exemption is void once any other actor can overwrite the helper (fail-closed, A175)", path, fi.Mode().Perm())
	}
	return nil
}

// helperOwnership extracts the two facts the A175 gate needs from a stat result.
// ok=false means the platform did not hand us a unix stat record, and the gate
// fails closed on that — an unknown owner is not a root owner.
func helperOwnership(fi os.FileInfo) (uid uint32, writableByOthers bool, ok bool) {
	st, isUnix := fi.Sys().(*syscall.Stat_t)
	if !isUnix {
		return 0, false, false
	}
	return st.Uid, fi.Mode().Perm()&0o022 != 0, true
}

// trustBundleCandidates are the PEM stores Go's own TLS stack falls back to on
// Linux, after the SSL_CERT_FILE/SSL_CERT_DIR overrides.
var trustBundleCandidates = []string{
	"/etc/ssl/certs/ca-certificates.crt",
	"/etc/pki/tls/certs/ca-bundle.crt",
}

// hasSystemTrustRoots reports whether this process can build ANY trust anchor for
// an https control plane. The plugin image is debian:bookworm-slim, whose default
// root store is EMPTY unless ca-certificates is installed, so an https
// CDS_VOLUME_PLUGIN_KMS_BASE_URL can pass every build gate and then fail every
// DEK call with "certificate signed by unknown authority" at runtime (A204①).
func hasSystemTrustRoots() bool {
	if f := os.Getenv("SSL_CERT_FILE"); f != "" {
		return usableTrustFile(f)
	}
	if d := os.Getenv("SSL_CERT_DIR"); d != "" {
		ents, err := os.ReadDir(d)
		return err == nil && len(ents) > 0
	}
	for _, p := range trustBundleCandidates {
		if usableTrustFile(p) {
			return true
		}
	}
	return false
}

func usableTrustFile(p string) bool {
	fi, err := os.Stat(p)
	return err == nil && !fi.IsDir() && fi.Size() > 0
}

// checkTrustRoots refuses an https control plane that this process cannot
// verify. It deliberately does not weaken anything: the plaintext-http side of
// the same default is gated in kms.go (A179).
func (c Config) checkTrustRoots() error {
	if !strings.HasPrefix(strings.ToLower(c.KMSBaseURL), "https://") {
		return nil
	}
	if c.KMSCAFile != "" || c.KMSInsecureSkipVerify {
		return nil
	}
	if hasSystemTrustRoots() {
		return nil
	}
	return fmt.Errorf("CDS_VOLUME_PLUGIN_KMS_BASE_URL is https but this process has no trust store " +
		"(no SSL_CERT_FILE/SSL_CERT_DIR, no /etc/ssl/certs/ca-certificates.crt or /etc/pki/tls/certs/ca-bundle.crt): " +
		"every DEK call would fail with x509 \"certificate signed by unknown authority\". Install ca-certificates in the " +
		"image or pin the control-plane bundle with CDS_VOLUME_PLUGIN_KMS_CA_FILE (fail-closed, A204①)")
}
