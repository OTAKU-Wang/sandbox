package main

// A202②③⑥⑧ unit tests for the CLI seams that carry an error out of the JSON
// output path. A202⑥ is a dropped-error defect: an encoder failure had no route
// to the exit code, so these tests aim at the seam that gives it one.

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var errGo04Encoder = errors.New("encoder exploded")

func go04FailingEncoder(any) ([]byte, error) { return nil, errGo04Encoder }

func go04RunEncoded(t *testing.T, enc encoder, args []string) (int, string, string) {
	t.Helper()
	return go04Capture(t, func() int { return runCLIWith(args, enc) })
}

// ── ② bounded data-file reader ─────────────────────────────────────────────

func TestA202Go04_ReadVolumeDataRefusesFromMetadataBeforeReading(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sparse.bin")
	f, err := os.Create(path)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if err := f.Truncate(64 << 20); err != nil {
		t.Fatalf("truncate: %v", err)
	}
	if err := f.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
	_, err = readVolumeData(path, 1<<20)
	if err == nil {
		t.Fatal("a 64 MiB file passed a 1 MiB bound")
	}
	for _, want := range []string{"67108864", "1048576", "CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("refusal must name %s, got %q", want, err)
		}
	}
}

func TestA202Go04_ReadVolumeDataAcceptsUpToTheBound(t *testing.T) {
	dir := t.TempDir()
	payload := []byte("0123456789")
	path := filepath.Join(dir, "data.bin")
	if err := os.WriteFile(path, payload, 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	got, err := readVolumeData(path, int64(len(payload)))
	if err != nil {
		t.Fatalf("exact-bound read: %v", err)
	}
	if string(got) != string(payload) {
		t.Fatalf("read %q, want %q", got, payload)
	}
	if _, err := readVolumeData(filepath.Join(dir, "absent.bin"), 1<<20); err == nil {
		t.Fatal("a missing data file must be an error")
	}
	if _, err := readVolumeData(dir, 1<<20); err == nil {
		t.Fatal("--data-file pointing at a directory must be an error, not an empty read")
	}
}

// ── ⑥ encoder errors reach the exit code ───────────────────────────────────

func TestA202Go04_EmitJSONReturnsEncoderError(t *testing.T) {
	if err := emitJSON(io.Discard, json.Marshal, map[string]string{"ok": "yes"}); err != nil {
		t.Fatalf("emitJSON of an encodable value: %v", err)
	}
	if err := emitJSON(io.Discard, json.Marshal, make(chan int)); err == nil {
		t.Fatal("emitJSON swallowed an encoder error")
	}
	if err := emitJSON(io.Discard, go04FailingEncoder, map[string]string{}); !errors.Is(err, errGo04Encoder) {
		t.Fatalf("emitJSON must wrap the encoder error, got %v", err)
	}
}

func TestA202Go04_CLIRunsEncoderErrorToNonZeroExit(t *testing.T) {
	go04Env(t, nil)
	args := []string{"ctl", "create", "--volume-id", "vol-1", "--name", "cds-data-vol-1"}

	code, stdout, stderr := go04Run(t, args...)
	if code != 0 {
		t.Fatalf("baseline ctl create exit=%d stderr=%s", code, stderr)
	}
	if err := json.Unmarshal([]byte(strings.TrimSpace(stdout)), new(map[string]any)); err != nil {
		t.Fatalf("baseline stdout is not one JSON document: %q (%v)", stdout, err)
	}

	// Same command, failing encoder: a failed encoder must never look like success
	// to a scripted deploy, so exit is non-zero and stdout stays empty.
	code, stdout, stderr = go04RunEncoded(t, go04FailingEncoder, args)
	if code == 0 {
		t.Fatalf("a failed JSON encoder exited 0 with stdout %q (A202⑥)", stdout)
	}
	if stdout != "" {
		t.Fatalf("a failed encoder must not print a result line, got stdout %q", stdout)
	}
	if !strings.Contains(stderr, errGo04Encoder.Error()) {
		t.Fatalf("the encoder failure must be reported, got stderr %q", stderr)
	}
}

// ── ③ serve-path boot gate ─────────────────────────────────────────────────

func TestA202Go04_ServeRefusesImpossibleSubprocTimeout(t *testing.T) {
	go04Env(t, map[string]string{
		"CDS_SM4_AEAD_BACKEND":                 "tongsuo",
		"CDS_VOLUME_PLUGIN_SM4_HELPER":         "/nonexistent/sm4_gcm",
		"CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS": "0",
	})
	err := serveCmd(nil)
	if err == nil || !strings.Contains(err.Error(), "CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS") {
		t.Fatalf("serve must refuse an unbounded helper budget naming the env key, got %v", err)
	}
}

// ── ⑧ structured crypto posture ────────────────────────────────────────────

func TestA202Go04_CryptoPostureIsHonestPerBackend(t *testing.T) {
	fallback := newCryptoPosture("aes_fallback", "aes-128-gcm(fallback,non-GM)")
	if fallback.GMCompliant || !fallback.Degraded {
		t.Fatalf("aes_fallback posture = %+v, want gm_compliant=false degraded=true", fallback)
	}
	if !strings.Contains(strings.ToLower(fallback.Disclosure), "sm4") {
		t.Fatalf("the fallback posture must name the inactive GM algorithm: %+v", fallback)
	}
	if !strings.Contains(fallback.ProductionRequires, "tongsuo") {
		t.Fatalf("the fallback posture must state the production setting: %+v", fallback)
	}
	gm := newCryptoPosture("tongsuo", "sm4-gcm(tongsuo)")
	if !gm.GMCompliant || gm.Degraded {
		t.Fatalf("tongsuo posture = %+v, want gm_compliant=true degraded=false", gm)
	}
}

func TestA202Go04_CryptoPostureLineIsMachineReadable(t *testing.T) {
	line, err := cryptoPostureLine(newCryptoPosture("aes_fallback", "aes-128-gcm(fallback,non-GM)"), json.Marshal)
	if err != nil {
		t.Fatalf("cryptoPostureLine: %v", err)
	}
	if !strings.HasPrefix(line, "crypto_posture=") {
		t.Fatalf("posture line must carry a stable grep key, got %q", line)
	}
	if strings.ContainsAny(line, "\n") {
		t.Fatalf("posture line must be one line, got %q", line)
	}
	var rec map[string]any
	body := strings.TrimPrefix(line, "crypto_posture=")
	if err := json.Unmarshal([]byte(body), &rec); err != nil {
		t.Fatalf("posture payload must be JSON, got %q: %v", body, err)
	}
	if v, ok := rec["gm_compliant"].(bool); !ok || v {
		t.Fatalf("posture payload must disclose gm_compliant=false, got %v", rec["gm_compliant"])
	}
}

func TestA202Go04_PostureEncoderFailureIsAnError(t *testing.T) {
	if _, err := cryptoPostureLine(newCryptoPosture("tongsuo", "sm4-gcm(tongsuo)"), go04FailingEncoder); err == nil {
		t.Fatal("an undisclosed posture must be an error, never a silently missing line")
	}
}
