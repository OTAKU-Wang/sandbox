package main

// A179 transport contract (docs/ocr-review-2026-10-18/ocr-verified-findings.md
// §A179, critical/活路径): the KMS leg carries plaintext DEK hex plus the
// service bearer token, so "生产传输是 mTLS" must be a property of the code,
// not of a comment. Pinned here BEFORE the fix:
//
//   1. a non-https endpoint is refused (fail-closed) unless the operator
//      explicitly opts in (env CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE /
//      `serve --allow-insecure-kms`);
//   2. TLS verification is never silently skipped — a control-plane CA is
//      injected with CDS_VOLUME_PLUGIN_KMS_CA_FILE, client certs with
//      _CLIENT_CERT/_CLIENT_KEY, and skipping verification is its own loud
//      option (never the default);
//   3. redirects are never followed (a 307/308 must not replay DEK bodies to
//      another host);
//   4. any allowed downgrade is disclosed loudly (boot WARNING + a queryable
//      transport mode), never presented as mTLS.
//
// 铁律 禁伪造: this file only asserts refusal/disclosure. It never accepts a
// silent cleartext path.

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// ── test PKI (real x509 chain: CA → server leaf + client leaf) ─────────────

type testPKI struct {
	caFile         string
	serverCertFile string
	serverKeyFile  string
	clientCertFile string
	clientKeyFile  string
	caPool         *x509.CertPool
	serverCert     tls.Certificate
	clientCert     tls.Certificate
}

func writePEM(t *testing.T, dir, name string, typ string, der []byte) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, pem.EncodeToMemory(&pem.Block{Type: typ, Bytes: der}), 0600); err != nil {
		t.Fatalf("write %s: %v", name, err)
	}
	return p
}

func newTestPKI(t *testing.T) *testPKI {
	t.Helper()
	dir := t.TempDir()

	mkKey := func() *ecdsa.PrivateKey {
		k, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			t.Fatalf("keygen: %v", err)
		}
		return k
	}
	caKey, srvKey, cliKey := mkKey(), mkKey(), mkKey()

	notBefore, notAfter := time.Now().Add(-time.Hour), time.Now().Add(time.Hour)
	caTmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(0x9b75d339),
		Subject:               pkix.Name{CommonName: "cds-test-control-plane-ca"},
		NotBefore:             notBefore,
		NotAfter:              notAfter,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTmpl, caTmpl, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatalf("ca: %v", err)
	}
	caCert, err := x509.ParseCertificate(caDER)
	if err != nil {
		t.Fatalf("parse ca: %v", err)
	}
	pool := x509.NewCertPool()
	pool.AddCert(caCert)

	sign := func(tmpl *x509.Certificate, priv *ecdsa.PrivateKey) []byte {
		der, err := x509.CreateCertificate(rand.Reader, tmpl, caCert, &priv.PublicKey, caKey)
		if err != nil {
			t.Fatalf("sign: %v", err)
		}
		return der
	}
	srvDER := sign(&x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: "cds-kms-test"},
		NotBefore:    notBefore, NotAfter: notAfter,
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:    []string{"localhost"},
		IPAddresses: []net.IP{net.ParseIP("127.0.0.1")},
	}, srvKey)
	cliDER := sign(&x509.Certificate{
		SerialNumber: big.NewInt(3),
		Subject:      pkix.Name{CommonName: "cds-volume-plugin"},
		NotBefore:    notBefore, NotAfter: notAfter,
		KeyUsage:    x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}, cliKey)

	p := &testPKI{caPool: pool}
	p.caFile = writePEM(t, dir, "ca.pem", "CERTIFICATE", caDER)
	p.serverCertFile = writePEM(t, dir, "server.pem", "CERTIFICATE", srvDER)
	p.clientCertFile = writePEM(t, dir, "client.pem", "CERTIFICATE", cliDER)
	p.serverKeyFile = keyFile(t, dir, "server.key", srvKey)
	p.clientKeyFile = keyFile(t, dir, "client.key", cliKey)
	p.serverCert = tls.Certificate{Certificate: [][]byte{srvDER}, PrivateKey: srvKey}
	p.clientCert = tls.Certificate{Certificate: [][]byte{cliDER}, PrivateKey: cliKey}
	return p
}

func keyFile(t *testing.T, dir, name string, k *ecdsa.PrivateKey) string {
	t.Helper()
	der, err := x509.MarshalECPrivateKey(k)
	if err != nil {
		t.Fatalf("marshal key: %v", err)
	}
	return writePEM(t, dir, name, "EC PRIVATE KEY", der)
}

// ── KMS wire stand-in, recording what actually crossed the transport ────────

type kmsCapture struct {
	mu        sync.Mutex
	requests  int
	dekBodies []string
	peerCN    string
}

func (k *kmsCapture) dekSeen() string {
	k.mu.Lock()
	defer k.mu.Unlock()
	return strings.Join(k.dekBodies, "|")
}

func (k *kmsCapture) count() int {
	k.mu.Lock()
	defer k.mu.Unlock()
	return k.requests
}

func (k *kmsCapture) handler(t *testing.T) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, r *http.Request) {
		var body []byte
		if r.Body != nil {
			body, _ = io.ReadAll(r.Body)
		}
		k.mu.Lock()
		k.requests++
		if strings.Contains(string(body), "dek_hex") {
			k.dekBodies = append(k.dekBodies, string(body))
		}
		if r.TLS != nil && len(r.TLS.PeerCertificates) > 0 {
			k.peerCN = r.TLS.PeerCertificates[0].Subject.CommonName
		}
		k.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/kms/volume-keys":
			fmt.Fprint(w, `{"key_id":"vol-dek-ref-1"}`)
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/distribute"):
			fmt.Fprint(w, `{"key_hex":"`+strings.Repeat("ab", dekLen)+`"}`)
		default:
			http.Error(w, "unexpected", http.StatusNotFound)
		}
	}
}

func startTLSServer(t *testing.T, pki *testPKI, h http.HandlerFunc, wantClientAuth bool) *httptest.Server {
	t.Helper()
	srv := httptest.NewUnstartedServer(h)
	cfg := &tls.Config{
		Certificates: []tls.Certificate{pki.serverCert},
		MinVersion:   tls.VersionTLS12,
	}
	if wantClientAuth {
		cfg.ClientAuth = tls.RequireAndVerifyClientCert
		cfg.ClientCAs = pki.caPool
	}
	srv.TLS = cfg
	srv.StartTLS()
	t.Cleanup(srv.Close)
	return srv
}

// captureLog pins "loud disclosure": the warning must actually be emitted.
func captureLog(t *testing.T) *strings.Builder {
	t.Helper()
	var buf strings.Builder
	out := log.Writer()
	log.SetOutput(&buf)
	t.Cleanup(func() { log.SetOutput(out) })
	return &buf
}

// ── 1. scheme gate: no plaintext without an explicit operator opt-in ────────

func TestA179_PlaintextEndpointRefusedWithoutOptIn(t *testing.T) {
	// Non-loopback plaintext is the leak channel the ledger names (跨节点部署).
	for _, u := range []string{
		"http://kms.cds.internal:8000",
		"http://10.0.0.5:8000",
		"http://localhost:8000", // a name is not a loopback IP literal → no exception
	} {
		c, err := NewKMSClient(u, "tok")
		if err == nil {
			t.Fatalf("plaintext endpoint %q accepted by default (DEK+token would leave in clear); client=%v", u, c)
		}
		if c != nil {
			t.Fatalf("refused endpoint %q still returned a client", u)
		}
		msg := err.Error()
		if !strings.Contains(msg, "https") {
			t.Errorf("error for %q must name the required scheme, got: %s", u, msg)
		}
		if !strings.Contains(msg, "CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE") {
			t.Errorf("error for %q must name the explicit opt-in switch, got: %s", u, msg)
		}
		// The options constructor must not be a back door either.
		if _, err := NewKMSClientWithOptions(u, "tok", KMSOptions{}); err == nil {
			t.Errorf("NewKMSClientWithOptions(%q) accepted plaintext with default options", u)
		}
	}
	// Malformed / non-HTTP endpoints are refused, not dialled anyway.
	for _, u := range []string{"", "kms.cds.internal:8000", "ftp://kms:8000", "https://", "https:///path", "http://[::1", "http://user:tok@kms.cds.internal:8000"} {
		if c, err := NewKMSClient(u, "tok"); err == nil || c != nil {
			t.Errorf("endpoint %q accepted (client=%v err=%v)", u, c, err)
		}
	}
}

func TestA179_OperatorOptInIsExplicit(t *testing.T) {
	const u = "http://kms.cds.internal:8000"
	c, err := NewKMSClientWithOptions(u, "tok", KMSOptions{AllowInsecureTransport: true})
	if err != nil {
		t.Fatalf("explicit opt-in must be honoured: %v", err)
	}
	if !c.Insecure() {
		t.Error("an opted-in plaintext client must report Insecure()==true")
	}
	if !strings.Contains(strings.ToLower(c.TransportMode()), "http-plaintext") {
		t.Errorf("TransportMode() must disclose the downgrade, got %q", c.TransportMode())
	}
}

func TestA179_LoopbackPlaintextIsLoudlyDisclosed(t *testing.T) {
	// Dev/test loopback (the config default, and what
	// tests/test_cds_volume_plugin.py drives) stays usable — but only as a LOUD,
	// queryable downgrade.
	seen := &kmsCapture{}
	srv := httptest.NewServer(seen.handler(t))
	defer srv.Close()

	logs := captureLog(t)
	c, err := NewKMSClient(srv.URL, "test-token")
	if err != nil {
		t.Fatalf("loopback dev endpoint must stay constructible: %v", err)
	}
	if !c.Insecure() {
		t.Error("cleartext KMS must report Insecure()==true")
	}
	if !strings.Contains(strings.ToLower(c.TransportMode()), "http-plaintext") {
		t.Errorf("TransportMode() = %q, want a plaintext disclosure", c.TransportMode())
	}
	got := logs.String()
	if !strings.Contains(got, "WARNING") || !strings.Contains(got, "KMS") ||
		!strings.Contains(strings.ToLower(got), "plaintext") {
		t.Errorf("plaintext KMS must log a WARNING naming the KMS transport, got: %q", got)
	}
	// And prove the disclosure matches reality: the DEK really does leave in clear.
	if _, err := c.RegisterDEK(context.Background(), "vol-1", "n", "aes_fallback", []byte{0xde, 0xad, 0xbe, 0xef}); err != nil {
		t.Fatalf("register over loopback KMS: %v", err)
	}
	if !strings.Contains(seen.dekSeen(), "deadbeef") {
		t.Errorf("test premise broken: mock KMS saw no plaintext dek_hex (%q)", seen.dekSeen())
	}
}

// ── 2. TLS material: CA injection is real, skip-verify is never the default ──

func TestA179_ControlPlaneCAIsHonouredAndSkipVerifyIsNeverDefault(t *testing.T) {
	pki := newTestPKI(t)
	seen := &kmsCapture{}
	srv := startTLSServer(t, pki, seen.handler(t), false)

	dek := []byte{0x01, 0x02, 0x03}
	ctx := context.Background()

	// (a) system roots + no CA file → the handshake must FAIL, not downgrade.
	c, err := NewKMSClient(srv.URL, "tok")
	if err != nil {
		t.Fatalf("https endpoint must construct: %v", err)
	}
	if c.Insecure() {
		t.Errorf("plain https client must not report insecure, mode=%q", c.TransportMode())
	}
	if _, err := c.RegisterDEK(ctx, "vol-1", "n", "aes_fallback", dek); err == nil {
		t.Fatal("untrusted control-plane cert was accepted without any explicit option (fail-open)")
	} else if !strings.Contains(strings.ToLower(err.Error()), "certificate") &&
		!strings.Contains(strings.ToLower(err.Error()), "tls") {
		t.Errorf("expected a TLS verification failure, got: %v", err)
	}

	// (b) explicit CA file → verification succeeds against the injected CA.
	c2, err := NewKMSClientWithOptions(srv.URL, "tok", KMSOptions{CAFile: pki.caFile})
	if err != nil {
		t.Fatalf("CAFile option: %v", err)
	}
	if ref, err := c2.RegisterDEK(ctx, "vol-1", "n", "aes_fallback", dek); err != nil || ref != "vol-dek-ref-1" {
		t.Fatalf("register over TLS with injected CA: ref=%q err=%v", ref, err)
	}
	if c2.Insecure() {
		t.Errorf("CA-pinned client must report secure, mode=%q", c2.TransportMode())
	}
	if !strings.Contains(strings.ToLower(c2.TransportMode()), "ca=") {
		t.Errorf("TransportMode() should name the CA source, got %q", c2.TransportMode())
	}

	// (c) insecure-skip-verify works ONLY as an explicit, loudly logged option.
	logs := captureLog(t)
	c3, err := NewKMSClientWithOptions(srv.URL, "tok", KMSOptions{InsecureSkipVerify: true})
	if err != nil {
		t.Fatalf("explicit skip-verify option: %v", err)
	}
	if _, err := c3.RegisterDEK(ctx, "vol-2", "n", "aes_fallback", dek); err != nil {
		t.Fatalf("skip-verify client should reach the TLS server: %v", err)
	}
	if !c3.Insecure() {
		t.Error("skip-verify must be reported as insecure")
	}
	l := strings.ToUpper(logs.String())
	if !strings.Contains(l, "WARNING") || !strings.Contains(l, "SKIP") {
		t.Errorf("skip-verify must be loudly logged, got: %q", logs.String())
	}

	// (d) an unusable CA file is a construction error, never a silent fall back
	//     to system roots or to no verification.
	if _, err := NewKMSClientWithOptions(srv.URL, "tok", KMSOptions{CAFile: filepath.Join(t.TempDir(), "absent.pem")}); err == nil {
		t.Error("missing CA file must fail closed")
	}
	garbage := filepath.Join(t.TempDir(), "garbage.pem")
	if err := os.WriteFile(garbage, []byte("not a certificate\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := NewKMSClientWithOptions(srv.URL, "tok", KMSOptions{CAFile: garbage}); err == nil {
		t.Error("unparsable CA file must fail closed")
	}
}

func TestA179_MTLSClientCertIsReal(t *testing.T) {
	pki := newTestPKI(t)
	seen := &kmsCapture{}
	srv := startTLSServer(t, pki, seen.handler(t), true)
	ctx := context.Background()
	dek := []byte{0x0a}

	// Without a client cert the control plane refuses the handshake: a client
	// that claims mTLS but presents nothing must fail, not succeed unauthenticated.
	cNo, err := NewKMSClientWithOptions(srv.URL, "tok", KMSOptions{CAFile: pki.caFile})
	if err != nil {
		t.Fatalf("construct: %v", err)
	}
	if _, err := cNo.RegisterDEK(ctx, "vol-1", "n", "aes_fallback", dek); err == nil {
		t.Fatal("client with no certificate succeeded against a client-cert-requiring KMS")
	}

	c, err := NewKMSClientWithOptions(srv.URL, "tok", KMSOptions{
		CAFile:         pki.caFile,
		ClientCertFile: pki.clientCertFile,
		ClientKeyFile:  pki.clientKeyFile,
	})
	if err != nil {
		t.Fatalf("mTLS options: %v", err)
	}
	if _, err := c.RegisterDEK(ctx, "vol-1", "n", "aes_fallback", dek); err != nil {
		t.Fatalf("mTLS register: %v", err)
	}
	if seen.peerCN != "cds-volume-plugin" {
		t.Errorf("control plane saw client cert CN %q", seen.peerCN)
	}
	if !strings.Contains(strings.ToLower(c.TransportMode()), "mtls") {
		t.Errorf("TransportMode() must report mtls when a client cert is loaded, got %q", c.TransportMode())
	}
	// Half a keypair is an operator mistake: refuse, don't serve half a claim.
	if _, err := NewKMSClientWithOptions(srv.URL, "tok", KMSOptions{
		CAFile: pki.caFile, ClientCertFile: pki.clientCertFile,
	}); err == nil {
		t.Error("client cert without key must fail closed")
	}
	if _, err := NewKMSClientWithOptions(srv.URL, "tok", KMSOptions{
		CAFile: pki.caFile, ClientKeyFile: pki.clientKeyFile,
	}); err == nil {
		t.Error("client key without cert must fail closed")
	}
}

// ── 3. redirects: DEK material is never replayed to a redirect target ───────

func TestA179_NeverFollowsRedirects(t *testing.T) {
	trap := &kmsCapture{}
	trapSrv := httptest.NewServer(trap.handler(t))
	defer trapSrv.Close()

	front := &kmsCapture{}
	frontSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		front.mu.Lock()
		front.requests++
		front.mu.Unlock()
		http.Redirect(w, r, trapSrv.URL+"/api/v1/kms/volume-keys", http.StatusPermanentRedirect) // 308 replays the body
	}))
	defer frontSrv.Close()

	c, err := NewKMSClient(frontSrv.URL, "tok")
	if err != nil {
		t.Fatalf("loopback construct: %v", err)
	}
	if _, err := c.RegisterDEK(context.Background(), "vol-1", "n", "aes_fallback", []byte{0x5e, 0xc0, 0xff, 0xee}); err == nil {
		t.Fatal("a 308 must not be reported as success")
	}
	if trap.count() != 0 {
		t.Errorf("client followed the redirect: trap saw %d requests, dek=%q", trap.count(), trap.dekSeen())
	}
	if trap.dekSeen() != "" {
		t.Errorf("DEK material was replayed to the redirect target: %q", trap.dekSeen())
	}
}

// ── 4. operator surface: env config and the serve flag ──────────────────────

func TestA179_ConfigWiring(t *testing.T) {
	ca := newTestPKI(t).caFile
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_BASE_URL", "https://127.0.0.1:8443")
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_CA_FILE", ca)
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_CLIENT_CERT", "")
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_CLIENT_KEY", "")
	cfg := LoadConfig()
	if cfg.KMSCAFile != ca {
		t.Fatalf("KMS TLS env not loaded: %+v", cfg)
	}
	if cfg.KMSAllowInsecure || cfg.KMSInsecureSkipVerify {
		t.Fatal("insecure switches must default to false")
	}
	opts := kmsOptionsFromConfig(cfg)
	if opts.CAFile != cfg.KMSCAFile || opts.ClientCertFile != cfg.KMSClientCertFile ||
		opts.ClientKeyFile != cfg.KMSClientKeyFile {
		t.Fatalf("config → KMSOptions mapping lost TLS material: %+v", opts)
	}
	if _, err := NewKMSClientFromConfig(cfg); err != nil {
		t.Fatalf("https config must construct: %v", err)
	}
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_CLIENT_CERT", "/etc/cds/kms-client.pem")
	if _, err := NewKMSClientFromConfig(LoadConfig()); err == nil {
		t.Fatal("client cert without its key must fail closed at boot")
	}
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_CLIENT_CERT", "")
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_CLIENT_KEY", "")

	// The plaintext switch reaches the client only through the explicit env key.
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_BASE_URL", "http://kms.cds.internal:8000")
	if _, err := NewKMSClientFromConfig(LoadConfig()); err == nil {
		t.Fatal("config with plaintext non-loopback URL must fail closed")
	}
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE", "1")
	c, err := NewKMSClientFromConfig(LoadConfig())
	if err != nil {
		t.Fatalf("explicit allow-insecure env must be honoured: %v", err)
	}
	if !c.Insecure() {
		t.Error("opted-in client must still report insecure")
	}
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_INSECURE_SKIP_VERIFY", "1")
	if !kmsOptionsFromConfig(LoadConfig()).InsecureSkipVerify {
		t.Error("CDS_VOLUME_PLUGIN_KMS_INSECURE_SKIP_VERIFY not wired into KMSOptions")
	}
}

func TestA179_ServeFlagIsTheSameOptIn(t *testing.T) {
	opts, err := parseServeFlags(nil)
	if err != nil {
		t.Fatalf("default serve flags: %v", err)
	}
	if opts.AllowInsecureKMS {
		t.Error("--allow-insecure-kms must default to false")
	}
	opts, err = parseServeFlags([]string{"--allow-insecure-kms"})
	if err != nil {
		t.Fatalf("flag parse: %v", err)
	}
	if !opts.AllowInsecureKMS {
		t.Error("--allow-insecure-kms must switch the transport opt-in on")
	}
	if _, err := parseServeFlags([]string{"--nope"}); err == nil {
		t.Error("unknown serve flag must be an error")
	}
	// The pre-existing timeout knob must survive alongside the TLS fields.
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_TIMEOUT_MS", "250")
	if got := kmsTimeout(); got != 250*time.Millisecond {
		t.Errorf("kmsTimeout() = %v, want 250ms", got)
	}
}
