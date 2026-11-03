package vol_test

import . "cds-volume-plugin/internal/vol"

// A202 (OCR 2026-10-18, medium) sub-items ②③⑦⑧ on the CLI / serve / self-test
// surface (cli.go, main.go, selftest.go), plus the A182 family recheck for
// these three files.
//
// Every assertion in THIS file is written against symbols that already exist at
// HEAD, so the RED reading is semantic and not a compile error:
//
//	bash .gate-scratch/red_tree.sh go04 -- cds-volume-plugin/cli.go \
//	     cds-volume-plugin/main.go cds-volume-plugin/selftest.go
//	cd /tmp/red_go04/cds-volume-plugin && rm a202_go04_units_test.go && \
//	  go test -count=1 -run 'TestA202Go04_' ./...
//
// Tests that need the new error-returning seams live in a202_go04_units_test.go.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// go04MockKMS is a self-contained stand-in for app/api/kms.py volume-keys: it
// only has to answer POST register, because the CLI paths under test stop before
// any unwrap. Self-contained on purpose — a202_go04 tests must not inherit the
// shape of another track's harness mid-round.
func go04MockKMS(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/api/v1/kms/volume-keys" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"key_id":"vkey-00000000000000000000000000042"}`))
			return
		}
		http.Error(w, "unexpected "+r.Method+" "+r.URL.Path, http.StatusNotFound)
	}))
	t.Cleanup(srv.Close)
	return srv
}

// go04Env points the plugin's env config at a throwaway volume root + the mock
// control plane. Everything the CLI under test needs, nothing it may touch.
func go04Env(t *testing.T, extra map[string]string) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "volumes")
	if err := os.MkdirAll(root, 0o700); err != nil {
		t.Fatalf("volume root: %v", err)
	}
	t.Setenv("CDS_VOLUME_PLUGIN_VOLUME_ROOT", root)
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_BASE_URL", go04MockKMS(t).URL)
	t.Setenv("CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN", "test-token")
	t.Setenv("CDS_SM4_AEAD_BACKEND", "aes_fallback")
	for k, v := range extra {
		t.Setenv(k, v)
	}
	return root
}

// go04Capture runs fn with stdout/stderr captured to files (small outputs, so no
// pipe-buffer deadlock and no timing dependency).
func go04Capture(t *testing.T, fn func() int) (code int, stdout, stderr string) {
	t.Helper()
	dir := t.TempDir()
	outF, err := os.Create(filepath.Join(dir, "stdout"))
	if err != nil {
		t.Fatalf("capture stdout: %v", err)
	}
	errF, err := os.Create(filepath.Join(dir, "stderr"))
	if err != nil {
		t.Fatalf("capture stderr: %v", err)
	}
	savedOut, savedErr := os.Stdout, os.Stderr
	os.Stdout, os.Stderr = outF, errF
	func() {
		defer func() { os.Stdout, os.Stderr = savedOut, savedErr }()
		code = fn()
	}()
	if err := outF.Close(); err != nil {
		t.Fatalf("close stdout capture: %v", err)
	}
	if err := errF.Close(); err != nil {
		t.Fatalf("close stderr capture: %v", err)
	}
	ob, err := os.ReadFile(outF.Name())
	if err != nil {
		t.Fatalf("read stdout capture: %v", err)
	}
	eb, err := os.ReadFile(errF.Name())
	if err != nil {
		t.Fatalf("read stderr capture: %v", err)
	}
	return code, string(ob), string(eb)
}

func go04Run(t *testing.T, args ...string) (int, string, string) {
	t.Helper()
	return go04Capture(t, func() int { return RunCLI(args) })
}

func go04JSONLine(t *testing.T, out string) map[string]any {
	t.Helper()
	var m map[string]any
	// The Python contract (tests/test_cds_volume_plugin.py) is json.loads(stdout),
	// i.e. exactly one JSON document on stdout — an extra line there would break it.
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &m); err != nil {
		t.Fatalf("stdout must stay one JSON document, got %q: %v", out, err)
	}
	return m
}

func go04Keys(m map[string]any) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

// ── ② cli.go: the data file is size-checked BEFORE it is read ──────────────

// TestA202Go04_CLIRefusesOversizeDataFileBeforeReading: cli.go used
// os.ReadFile(*dataFile) with no bound and only PutVolumeData rejected the size
// afterwards, so a hostile/oversized --data-file was fully materialised in the
// plugin's heap (as root) before anything said no. The refusal must come from a
// pre-read check that names the knob the operator has to raise.
func TestA202Go04_CLIRefusesOversizeDataFileBeforeReading(t *testing.T) {
	go04Env(t, map[string]string{"CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES": "4096"})
	if code, _, se := go04Run(t, "ctl", "create", "--volume-id", "vol-1", "--name", "cds-data-vol-1"); code != 0 {
		t.Fatalf("ctl create exit=%d stderr=%s", code, se)
	}
	dataFile := filepath.Join(t.TempDir(), "data.bin")
	f, err := os.Create(dataFile)
	if err != nil {
		t.Fatalf("create data file: %v", err)
	}
	// Sparse: 16 MiB of declared size, no content. Its size is knowable from the
	// directory entry, so a bounded reader never allocates for it.
	if err := f.Truncate(16 << 20); err != nil {
		t.Fatalf("truncate data file: %v", err)
	}
	if err := f.Close(); err != nil {
		t.Fatalf("close data file: %v", err)
	}

	code, so, se := go04Run(t, "ctl", "put", "--volume-id", "vol-1", "--data-file", dataFile)
	if code == 0 {
		t.Fatalf("a 16 MiB data file was accepted under a 4096-byte cap (exit 0, stdout %q)", so)
	}
	if !strings.Contains(se, "CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES") {
		t.Fatalf("the oversize refusal must name CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES, which proves the "+
			"size was checked from the file metadata before the read; got stderr %q — at HEAD the whole "+
			"blob was read into memory first and only PutVolumeData rejected it (A202②)", se)
	}
	if !strings.Contains(se, "16777216") {
		t.Fatalf("the refusal must disclose the size it refused, got %q", se)
	}
	if so != "" {
		t.Fatalf("a refused put must print no result JSON, got stdout %q", so)
	}
}

// ── ③ boot gate: a subprocess budget that cannot bound anything ────────────

// TestA202Go04_CLIBootRefusesImpossibleSubprocBudget: Config.Validate() never
// looked at SubprocTimeout, so the plugin booted, passed the KMS/transport
// checks and only then failed every Seal/Open — with a self-test error that
// blames the helper instead of the config. 0/negative can never bound a helper
// call, so boot must refuse it and name the env key.
func TestA202Go04_CLIBootRefusesImpossibleSubprocTimeout(t *testing.T) {
	go04Env(t, map[string]string{
		"CDS_SM4_AEAD_BACKEND":                 "tongsuo",
		"CDS_VOLUME_PLUGIN_SM4_HELPER":         "/nonexistent/sm4_gcm",
		"CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS": "0",
	})
	code, _, se := go04Run(t, "selftest")
	if code == 0 {
		t.Fatalf("exit must be non-zero for an unstartable config")
	}
	if !strings.Contains(se, "CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS") {
		t.Fatalf("a 0 ms helper budget must be refused as a config error naming "+
			"CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS; got exit=%d stderr=%q — without that naming, the operator "+
			"sees a crypto failure and blames the Tongsuo helper instead of the config (A202③)", code, se)
	}
	if !strings.Contains(se, "config") {
		t.Fatalf("the refusal must be reported as a config problem, not as a self-test failure: %q", se)
	}
}

// TestA202Go04_CLIKeepsSubSecondSubprocTimeoutLegal is the counterpart lock: a
// sub-second budget is a legitimate operator choice (A176), so the plugin must
// reach the crypto path with it instead of refusing it as a config error.
func TestA202Go04_CLIKeepsSubSecondSubprocTimeoutLegal(t *testing.T) {
	go04Env(t, map[string]string{
		"CDS_SM4_AEAD_BACKEND":                 "tongsuo",
		"CDS_VOLUME_PLUGIN_SM4_HELPER":         "/nonexistent/sm4_gcm",
		"CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS": "200",
	})
	code, _, se := go04Run(t, "selftest")
	if strings.Contains(se, "config:") || code == 2 {
		t.Fatalf("a 200 ms helper budget is legal (A176); boot must not refuse it as a config error: "+
			"exit=%d stderr=%q", code, se)
	}
	if code == 0 {
		t.Fatalf("the helper is missing on purpose — the self-test must still fail (exit 3), got exit 0")
	}
}

// ── ⑦ self-test: prove encryption happened, and that AAD is bound ──────────

// go04PlainEchoCipher authenticates honestly but never encrypts: Seal hands back
// the PLAINTEXT bytes as the ciphertext under a genuine AES-GCM tag, and Open
// re-derives that tag to verify. Every round-trip and every ct/tag tamper check
// passes — only an "did encryption actually happen" assertion can see it.
type go04PlainEchoCipher struct{ real Cipher }

func (c *go04PlainEchoCipher) Name() string { return "aes-128-gcm(never-encrypts,non-GM)" }

func (c *go04PlainEchoCipher) Seal(key, nonce, aad, plaintext []byte) ([]byte, []byte, error) {
	_, tag, err := c.real.Seal(key, nonce, aad, plaintext)
	if err != nil {
		return nil, nil, err
	}
	return append([]byte(nil), plaintext...), tag, nil
}

func (c *go04PlainEchoCipher) Open(key, nonce, aad, ciphertext, tag []byte) ([]byte, error) {
	// ciphertext IS the plaintext here: re-seal it and compare the tag.
	_, want, err := c.real.Seal(key, nonce, aad, ciphertext)
	if err != nil {
		return nil, err
	}
	if !bytes.Equal(want, tag) {
		return nil, fmt.Errorf("auth mismatch (fail-closed)")
	}
	return append([]byte(nil), ciphertext...), nil
}

// go04AADBlindCipher encrypts for real but drops the AAD on the floor — the
// classic honest-looking cipher that makes volume AAD (the volume label, spec
// §2.4) decorative. HEAD's self-test had no AAD negative case at all.
type go04AADBlindCipher struct{ real Cipher }

func (c *go04AADBlindCipher) Name() string { return "aes-128-gcm(aad-blind,non-GM)" }

func (c *go04AADBlindCipher) Seal(key, nonce, aad, plaintext []byte) ([]byte, []byte, error) {
	return c.real.Seal(key, nonce, nil, plaintext)
}

func (c *go04AADBlindCipher) Open(key, nonce, aad, ciphertext, tag []byte) ([]byte, error) {
	return c.real.Open(key, nonce, nil, ciphertext, tag)
}

func TestA202Go04_SelfTestRejectsCipherThatNeverEncrypts(t *testing.T) {
	err := RunSelfTest(&go04PlainEchoCipher{real: NewAESGCMCipher()})
	if err == nil {
		t.Fatalf("self-test PASSED a cipher whose ciphertext is the plaintext: it only proves " +
			"round-trip consistency, so an identity/no-op backend serves production while at-rest " +
			"data is unencrypted (A202⑦ honest-degradation)")
	}
}

func TestA202Go04_SelfTestRejectsAADBlindCipher(t *testing.T) {
	err := RunSelfTest(&go04AADBlindCipher{real: NewAESGCMCipher()})
	if err == nil {
		t.Fatalf("self-test PASSED a cipher that ignores AAD: with no AAD negative case a backend " +
			"that does not bind the volume label passes boot, and ciphertext becomes swappable " +
			"between volumes (A202⑦)")
	}
}

func TestA202Go04_SelfTestStillPassesRealBackends(t *testing.T) {
	// Counter-lock for the two assertions above: the real dev backend must still
	// pass every negative the self-test runs.
	if err := RunSelfTest(NewAESGCMCipher()); err != nil {
		t.Fatalf("RunSelfTest(aes_fallback) = %v, want nil", err)
	}
}

// TestA202Go04_SelfTestFailureDisclosesStatusNotMaterial: the self-test runs on a
// 4 KiB plaintext and a random DEK. A failure line must describe the property
// that broke — never echo the plaintext, the key or the AAD it just handled.
func TestA202Go04_SelfTestFailureDisclosesStatusNotMaterial(t *testing.T) {
	// First bytes of RunSelfTest's deterministic plaintext pattern (byte(i*7)).
	material := make([]byte, 16)
	for i := range material {
		material[i] = byte(i * 7)
	}
	for _, c := range []Cipher{
		&go04PlainEchoCipher{real: NewAESGCMCipher()},
		&go04AADBlindCipher{real: NewAESGCMCipher()},
	} {
		err := RunSelfTest(c)
		if err == nil {
			t.Fatalf("%s: self-test accepted a broken cipher, so there is no failure line to audit", c.Name())
		}
		msg := err.Error()
		if strings.Contains(msg, "cds-volume-plugin-selftest") {
			t.Fatalf("%s: failure line echoes the self-test AAD: %q", c.Name(), msg)
		}
		if bytes.ContainsRune([]byte(msg), 0x00) || bytes.Contains([]byte(msg), material) {
			t.Fatalf("%s: failure line carries self-test plaintext/key bytes instead of a status: %q", c.Name(), msg)
		}
	}
}

// ── ⑧ non-GM degrade: structured disclosure, not just a WARNING ────────────

// TestA202Go04_SelftestJSONDisclosesNonGMPosture: the aes_fallback path is a real
// degrade — the at-rest cipher is not the GM/T SM4-GCM the design specifies — so a
// human WARNING line alone is not honest disclosure: a scripted deploy cannot read
// it. The machine-readable self-test result must state the posture itself.
func TestA202Go04_SelftestJSONDisclosesNonGMPosture(t *testing.T) {
	go04Env(t, map[string]string{"CDS_SM4_AEAD_BACKEND": "aes_fallback"})
	code, so, se := go04Run(t, "selftest")
	if code != 0 {
		t.Fatalf("selftest exit=%d stderr=%s, want 0", code, se)
	}
	out := go04JSONLine(t, so)
	if s, _ := out["cipher"].(string); !strings.Contains(s, "aes") {
		t.Fatalf("selftest output lost its cipher field, got %v", out)
	}
	gm, ok := out["gm_compliant"].(bool)
	if !ok {
		t.Fatalf("selftest output must disclose gm_compliant as a boolean (A202⑧): the non-GM "+
			"degrade was only a stderr WARNING, invisible to a deploy gate; got keys %v", go04Keys(out))
	}
	if gm {
		t.Fatalf("gm_compliant=true while CDS_SM4_AEAD_BACKEND=aes_fallback is a false disclosure")
	}
	if deg, ok := out["degraded"].(bool); !ok || !deg {
		t.Fatalf("degraded must be true on the fallback backend, got %v", out["degraded"])
	}
	if b, _ := out["aead_backend"].(string); b != "aes_fallback" {
		t.Fatalf("aead_backend must name the configured backend, got %q", b)
	}
	if d, _ := out["disclosure"].(string); !strings.Contains(strings.ToLower(d), "sm4") {
		t.Fatalf("disclosure must name the GM algorithm that is inactive, got %q", d)
	}
}

// ── A182 family recheck on these three files ───────────────────────────────

// TestA202Go04_AnnotationErrorDoesNotEchoPrivateData: the A182 family is
// "raw payload bytes pasted into an error that ends up in a log / gRPC status".
// kms.go's half is go-01's; this is the same shape in cli.go's annotation
// parser, whose entry dump contains private_data — the DEK reference CDS hands
// the plugin (spec §2.4).
func TestA202Go04_AnnotationErrorDoesNotEchoPrivateData(t *testing.T) {
	const canary = "vkey-00000000000000000000000000042"
	inner, err := json.Marshal(map[string]string{"session_dek_ref": canary})
	if err != nil {
		t.Fatalf("build private_data: %v", err)
	}
	payload, err := json.Marshal([]map[string]string{
		{"driver": PluginName, "private_data": string(inner)},
	})
	if err != nil {
		t.Fatalf("build annotation: %v", err)
	}
	err = ParseAnnotationCmd(string(payload))
	if err == nil {
		t.Fatal("an annotation entry without a name was accepted")
	}
	if !strings.Contains(err.Error(), "name") {
		t.Fatalf("the refusal must say which field is missing, got %v", err)
	}
	if strings.Contains(err.Error(), canary) {
		t.Fatalf("A182 family still live in cli.go: the error carries the raw annotation entry, so the "+
			"DEK reference ends up in whatever captured this error (operator log, caller). got: %v", err)
	}
}
