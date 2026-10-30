package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// KMSClient talks to the CDS control-plane KMS for volume DEK lifecycle.
//
// Documented contract (SS-10 CB-03 spec step 2/4; control-plane routes live in
// app/api/kms.py).
// The plugin is fail-closed: any transport/HTTP error aborts
// the caller's operation, there is no offline mode.
//
//	Register: POST   {base}/api/v1/kms/volume-keys
//	          body {volume_id, name, dek_hex, cipher}     -> {key_id}
//	Unwrap:   POST   {base}/api/v1/kms/volume-keys/{ref}/distribute
//	          body {session_id}                           -> {key_hex}
//	Revoke:   DELETE {base}/api/v1/kms/volume-keys/{ref} -> 204
//
// Auth: Authorization: Bearer <CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN>.
//
// Transport (A179, OCR 2026-10-18 — "mTLS 在代码里不存在"): this leg carries
// plaintext DEK hex plus the service bearer token, so https is MANDATORY. The
// control-plane CA is injected with CDS_VOLUME_PLUGIN_KMS_CA_FILE and a real
// mTLS keypair with _CLIENT_CERT/_CLIENT_KEY. Cleartext http is accepted only
// for a loopback IP literal (dev/test, e.g. the config default) or behind the
// explicit CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE=1 / `serve --allow-insecure-kms`
// opt-in, and either case logs a WARNING at boot and is reported by
// TransportMode() — a plaintext transport is never presented as mTLS.
type KMSClient struct {
	base     string
	token    string
	hc       *http.Client
	mode     string // disclosure, e.g. "https+mtls(ca=/etc/cds/ca.pem)"
	insecure bool   // DEK crosses the wire unencrypted or unauthenticated
}

const (
	envKMSTimeoutMS = "CDS_VOLUME_PLUGIN_KMS_TIMEOUT_MS"

	// KMS transport switches, read by LoadConfig (A179).
	EnvKMSAllowInsecure   = "CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE"
	EnvKMSInsecureSkipTLS = "CDS_VOLUME_PLUGIN_KMS_INSECURE_SKIP_VERIFY"
	EnvKMSCAFile          = "CDS_VOLUME_PLUGIN_KMS_CA_FILE"
	EnvKMSClientCert      = "CDS_VOLUME_PLUGIN_KMS_CLIENT_CERT"
	EnvKMSClientKey       = "CDS_VOLUME_PLUGIN_KMS_CLIENT_KEY"
)

func kmsTimeout() time.Duration {
	if v, err := strconv.ParseInt(os.Getenv(envKMSTimeoutMS), 10, 64); err == nil && v > 0 {
		return time.Duration(v) * time.Millisecond
	}
	return 30 * time.Second
}

// KMSOptions configures a KMSClient's transport. The zero value is the
// production-safe default: https only, server certificate verified against the
// system roots, no client certificate.
type KMSOptions struct {
	// CAFile pins the control-plane CA bundle (PEM, appended to system roots).
	CAFile string
	// ClientCertFile/ClientKeyFile are this node's mTLS keypair.
	ClientCertFile string
	ClientKeyFile  string
	// AllowInsecureTransport opts into cleartext http for a NON-loopback
	// endpoint. Operator-only, never implied, always logged.
	AllowInsecureTransport bool
	// InsecureSkipVerify disables server-certificate verification. Test-only,
	// never the default, always logged.
	InsecureSkipVerify bool
	// Timeout bounds one request; zero falls back to kmsTimeout().
	Timeout time.Duration
}

// NewKMSClient is the safe default constructor: https endpoints only, verified
// against the system trust store. Use NewKMSClientWithOptions for a
// control-plane CA / mTLS keypair, or NewKMSClientFromConfig for env config.
func NewKMSClient(baseURL, token string) (*KMSClient, error) {
	return NewKMSClientWithOptions(baseURL, token, KMSOptions{})
}

// NewKMSClientFromConfig builds the client from the operator's config.
func NewKMSClientFromConfig(cfg Config) (*KMSClient, error) {
	return NewKMSClientWithOptions(cfg.KMSBaseURL, cfg.KMSToken, kmsOptionsFromConfig(cfg))
}

func kmsOptionsFromConfig(cfg Config) KMSOptions {
	return KMSOptions{
		CAFile:                 cfg.KMSCAFile,
		ClientCertFile:         cfg.KMSClientCertFile,
		ClientKeyFile:          cfg.KMSClientKeyFile,
		AllowInsecureTransport: cfg.KMSAllowInsecure,
		InsecureSkipVerify:     cfg.KMSInsecureSkipVerify,
	}
}

// NewKMSClientWithOptions validates the endpoint and builds the transport.
// Refusals here are fail-closed: an unusable CA or half a keypair aborts
// construction instead of quietly weakening verification.
func NewKMSClientWithOptions(baseURL, token string, opts KMSOptions) (*KMSClient, error) {
	endpoint, plaintext, err := validateKMSEndpoint(baseURL, opts.AllowInsecureTransport)
	if err != nil {
		return nil, err
	}
	tlsCfg, err := kmsTLSConfig(opts)
	if err != nil {
		return nil, err
	}

	mode := "https(system-ca)"
	if opts.CAFile != "" {
		mode = "https(ca=" + opts.CAFile + ")"
	}
	if len(tlsCfg.Certificates) > 0 {
		mode = "https+mtls" + strings.TrimPrefix(mode, "https")
	}
	if opts.InsecureSkipVerify {
		mode += "+insecure-skip-verify"
		log.Printf("WARNING: KMS server-certificate verification DISABLED (INSECURE-SKIP-VERIFY) for %s — DEK material goes to an UNVERIFIED peer; test networks only", baseURL)
	}
	if plaintext {
		if opts.AllowInsecureTransport {
			mode = "http-plaintext(operator-opt-in)"
		} else {
			mode = "http-plaintext(loopback-dev)"
		}
		log.Printf("WARNING: KMS transport is PLAINTEXT (mode=%s endpoint=%s): DEK hex and the service bearer token cross the wire UNENCRYPTED. Production uses https with "+EnvKMSCAFile+" (mTLS via "+EnvKMSClientCert+"/"+EnvKMSClientKey+"); "+EnvKMSAllowInsecure+"=1 is what allows this for a non-loopback endpoint", mode, baseURL)
	}

	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = kmsTimeout()
	}
	hc := &http.Client{
		Timeout: timeout,
		// A 307/308 replays the request body, and this body is DEK material: it
		// is never handed to a second endpoint (A179).
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return fmt.Errorf("kms redirect to %s refused after %d hop(s): DEK material is never replayed", req.URL, len(via))
		},
	}
	if !plaintext {
		hc.Transport = &http.Transport{
			TLSClientConfig:     tlsCfg,
			Proxy:               http.ProxyFromEnvironment,
			MaxIdleConnsPerHost: 4,
			IdleConnTimeout:     30 * time.Second,
		}
	}
	return &KMSClient{
		base:     strings.TrimRight(endpoint.String(), "/"),
		token:    token,
		hc:       hc,
		mode:     mode,
		insecure: plaintext || opts.InsecureSkipVerify,
	}, nil
}

// validateKMSEndpoint enforces the scheme. Cleartext is refused unless the
// operator opted in or the host is a loopback IP literal; a hostname is not
// treated as loopback, because name resolution is not the plugin's trust anchor.
func validateKMSEndpoint(baseURL string, allowInsecure bool) (*url.URL, bool, error) {
	trimmed := strings.TrimSpace(baseURL)
	if trimmed == "" {
		return nil, false, errors.New("kms endpoint unset: this node needs an https:// control-plane URL")
	}
	u, err := url.Parse(trimmed)
	if err != nil {
		return nil, false, fmt.Errorf("kms endpoint %q unparsable: %w", baseURL, err)
	}
	if u.Host == "" || u.Hostname() == "" {
		return nil, false, fmt.Errorf("kms endpoint %q has no host: expected https://<control-plane>[:port][/prefix]", baseURL)
	}
	if u.User != nil {
		return nil, false, fmt.Errorf("kms endpoint %q must not carry credentials in the URL: the service token belongs in the Authorization header", baseURL)
	}
	switch strings.ToLower(u.Scheme) {
	case "https":
		u.Scheme = "https"
		return u, false, nil
	case "http":
		if allowInsecure {
			u.Scheme = "http"
			return u, true, nil
		}
		if ip := net.ParseIP(u.Hostname()); ip != nil && ip.IsLoopback() {
			u.Scheme = "http"
			return u, true, nil
		}
		return nil, false, fmt.Errorf("kms endpoint %q is cleartext http: DEK material and the service token would leave this node unencrypted. Use an https:// endpoint, or set %s=1 to accept that downgrade knowingly", baseURL, EnvKMSAllowInsecure)
	default:
		return nil, false, fmt.Errorf("kms endpoint %q has scheme %q: only https is supported (cleartext http needs %s=1, dev only)", baseURL, u.Scheme, EnvKMSAllowInsecure)
	}
}

func kmsTLSConfig(opts KMSOptions) (*tls.Config, error) {
	cfg := &tls.Config{MinVersion: tls.VersionTLS12}
	if opts.CAFile != "" {
		pemBytes, err := os.ReadFile(opts.CAFile)
		if err != nil {
			return nil, fmt.Errorf("kms CA file %s: %w", opts.CAFile, err)
		}
		pool, err := x509.SystemCertPool()
		if err != nil || pool == nil {
			pool = x509.NewCertPool()
		}
		if !pool.AppendCertsFromPEM(pemBytes) {
			return nil, fmt.Errorf("kms CA file %s contains no parsable certificate (fail-closed)", opts.CAFile)
		}
		cfg.RootCAs = pool
	}
	if (opts.ClientCertFile == "") != (opts.ClientKeyFile == "") {
		return nil, fmt.Errorf("kms mTLS needs both %s and %s (one is unset — refusing to claim mTLS without a keypair)", EnvKMSClientCert, EnvKMSClientKey)
	}
	if opts.ClientCertFile != "" {
		cert, err := tls.LoadX509KeyPair(opts.ClientCertFile, opts.ClientKeyFile)
		if err != nil {
			return nil, fmt.Errorf("kms client keypair: %w", err)
		}
		cfg.Certificates = []tls.Certificate{cert}
	}
	cfg.InsecureSkipVerify = opts.InsecureSkipVerify
	return cfg, nil
}

// TransportMode discloses the transport actually in use (boot log, ops output).
func (c *KMSClient) TransportMode() string { return c.mode }

// Insecure reports whether DEK material crosses the wire unencrypted or
// unauthenticated.
func (c *KMSClient) Insecure() bool { return c.insecure }

// ErrKMSUnavailable marks transport-level failures for callers that classify
// key-operation aborts.
var ErrKMSUnavailable = errors.New("kms unavailable")

// kmsKeyRefPattern is the KMS ref grammar the control plane actually mints
// (`vkey-<32 hex>`, `dek-<uuid>-<8 hex>`, `…-rotated-<8 hex>`). The ref comes
// back from the control plane and is then concatenated into a URL PATH, so it
// is validated here rather than trusted (A181).
func validKMSRef(ref string) bool {
	if len(ref) == 0 || len(ref) > 128 {
		return false
	}
	for i := 0; i < len(ref); i++ {
		c := ref[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
		case c == '-' || c == '_':
		default:
			return false
		}
	}
	return true
}

// refPath validates a KMS ref and escapes it for its one path segment. A ref
// carrying a separator would otherwise move the request to another resource —
// /volume-keys/{ref}/distribute is the route that RELEASES key material (A181).
func refPath(op, ref string) (string, error) {
	if !validKMSRef(ref) {
		return "", fmt.Errorf("kms %s: refusing key ref of %d byte(s): expected 1-128 of [A-Za-z0-9_-] (fail-closed)", op, len(ref))
	}
	return url.PathEscape(ref), nil
}

// kmsBodyDigest reduces an upstream error body to one bounded, single-line
// digest. Error strings reach the log stream verbatim, and a gateway/proxy error
// page is both unbounded and full of markup (A182).
func kmsBodyDigest(data []byte) string {
	const keep = 200
	var b strings.Builder
	for i := 0; i < len(data) && b.Len() < keep; i++ {
		switch c := data[i]; {
		case c == '\n' || c == '\r' || c == '\t':
			b.WriteByte(' ')
		case c < 0x20 || c == 0x7f:
		default:
			b.WriteByte(c)
		}
	}
	if len(data) > keep {
		return fmt.Sprintf("%s… (+%d more bytes)", b.String(), len(data)-keep)
	}
	return b.String()
}

// kmsCall describes one control-plane round trip.
type kmsCall struct {
	method string
	path   string // already escaped by the caller
	body   any
	out    any // decode target; nil when the call returns no payload
	// expect are the status codes the wire contract allows. Anything else —
	// including another 2xx — is a failure (A180).
	expect []int
	// needBody marks the calls whose reply IS the payload: an empty 2xx body is
	// a failure, never a zero-value success (A180).
	needBody bool
	// echoBody is false on the calls whose request carries key material: an
	// upstream echo of the request must never reach the error string (A182).
	echoBody bool
}

func statusList(codes []int) string {
	parts := make([]string, len(codes))
	for i, c := range codes {
		parts[i] = strconv.Itoa(c)
	}
	return strings.Join(parts, "/")
}

func (c *KMSClient) do(ctx context.Context, call kmsCall) error {
	var rd io.Reader
	if call.body != nil {
		b, err := json.Marshal(call.body)
		if err != nil {
			return err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, call.method, c.base+call.path, rd)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return fmt.Errorf("kms %s %s: %w (fail-closed)", call.method, call.path, err)
	}
	defer resp.Body.Close()
	data, readErr := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if readErr != nil {
		// A half-read reply says nothing about what the control plane did, so it
		// can never be reported as success (A180).
		return fmt.Errorf("kms %s %s: response body unreadable after HTTP %d: %w (fail-closed)",
			call.method, call.path, resp.StatusCode, readErr)
	}
	if !statusAllowed(resp.StatusCode, call.expect) {
		body := "response body withheld (key-material call)"
		if call.echoBody {
			body = kmsBodyDigest(data)
		}
		return fmt.Errorf("kms %s %s: HTTP %d, expected %s: %s (fail-closed)",
			call.method, call.path, resp.StatusCode, statusList(call.expect), body)
	}
	if call.out == nil {
		return nil
	}
	if len(data) == 0 {
		return fmt.Errorf("kms %s %s: empty response body, expected a JSON reply (fail-closed)", call.method, call.path)
	}
	if err := json.Unmarshal(data, call.out); err != nil {
		return fmt.Errorf("kms %s %s: bad response: %w (fail-closed)", call.method, call.path, err)
	}
	return nil
}

func statusAllowed(code int, expect []int) bool {
	for _, want := range expect {
		if code == want {
			return true
		}
	}
	return false
}

// RegisterDEK registers a plugin-generated volume DEK and returns the
// wrapped-key reference (key_id) that goes into private_data. `cipher` is this
// plugin's Cipher.Name(): the control plane records it verbatim so the audit
// record states which cipher actually sealed the volume instead of assuming
// SM4-GCM (the default backend is the AES fallback).
func (c *KMSClient) RegisterDEK(ctx context.Context, volumeID, name, cipher string, dek []byte) (string, error) {
	var resp struct {
		KeyID string `json:"key_id"`
	}
	err := c.do(ctx, kmsCall{
		method: http.MethodPost,
		path:   "/api/v1/kms/volume-keys",
		body: map[string]string{
			"volume_id": volumeID,
			"name":      name,
			"dek_hex":   hex.EncodeToString(dek),
			"cipher":    cipher,
		},
		out:      &resp,
		expect:   []int{http.StatusCreated, http.StatusOK},
		needBody: true,
	})
	if err != nil {
		return "", err
	}
	if resp.KeyID == "" {
		return "", fmt.Errorf("kms register: empty key_id (fail-closed)")
	}
	if !validKMSRef(resp.KeyID) {
		return "", fmt.Errorf("kms register: control plane returned an unusable key_id of %d byte(s): "+
			"expected 1-128 of [A-Za-z0-9_-] (fail-closed)", len(resp.KeyID))
	}
	return resp.KeyID, nil
}

// UnwrapDEK fetches the plaintext DEK for a session (distribute_key
// semantics: session-scoped, TTL — reuse per spec step 4).
func (c *KMSClient) UnwrapDEK(ctx context.Context, ref, sessionID string) ([]byte, error) {
	var resp struct {
		KeyHex string `json:"key_hex"`
	}
	path, err := refPath("distribute", ref)
	if err != nil {
		return nil, err
	}
	err = c.do(ctx, kmsCall{
		method:   http.MethodPost,
		path:     "/api/v1/kms/volume-keys/" + path + "/distribute",
		body:     map[string]string{"session_id": sessionID},
		out:      &resp,
		expect:   []int{http.StatusOK},
		needBody: true,
		echoBody: true,
	})
	if err != nil {
		return nil, err
	}
	dek, err := hex.DecodeString(resp.KeyHex)
	if err != nil || len(dek) != dekLen {
		return nil, fmt.Errorf("kms unwrap: bad key_hex (fail-closed)")
	}
	return dek, nil
}

// RevokeDEK destroys the wrapped DEK at the control plane. Success is the
// contract's 204 and nothing else: a false "revoked" here lets Destroy report a
// crypto-erase while the key is still ACTIVE (A180).
func (c *KMSClient) RevokeDEK(ctx context.Context, ref string) error {
	path, err := refPath("revoke", ref)
	if err != nil {
		return err
	}
	return c.do(ctx, kmsCall{
		method:   http.MethodDelete,
		path:     "/api/v1/kms/volume-keys/" + path,
		expect:   []int{http.StatusNoContent},
		echoBody: true,
	})
}
