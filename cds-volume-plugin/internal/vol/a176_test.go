package vol_test

import . "cds-volume-plugin/internal/vol"

// A176 (OCR 2026-10-18, medium) — CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS is a
// MILLISECOND knob, but the cipher carried it as `int` SECONDS through
// int(cfg.SubprocTimeout.Seconds()). Any sub-second value (200/500, to cut a
// long tail) or 0/negative collapsed to 0, so `time.After(0)` fired before the
// helper could be scheduled: EVERY Seal/Open failed with
// "timeout after 0s (fail-closed)", the volume driver was completely dead, and
// the message blamed the Tongsuo helper's speed instead of the truncated config.
//
// No libtongsuo here, so these tests drive the subprocess contract with a
// /bin/sh stand-in helper through the real production constructor NewCipher().

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// g02aHelperScript writes a stand-in sm4_gcm helper and returns its path.
// `body` is the shell program; it must consume the framed stdin, because the
// cipher writes the key frame to it.
func g02aHelperScript(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "fake-sm4-gcm")
	script := "#!/bin/sh\n" + body + "\n"
	if err := os.WriteFile(path, []byte(script), 0o700); err != nil {
		t.Fatalf("write helper stand-in: %v", err)
	}
	return path
}

// g02aHelperEcho answers with 32 zero bytes: enough for tag(16)+body(16), so
// the cipher's own output-length checks pass and only the timeout logic is under test.
const g02aHelperEcho = "cat >/dev/null\ndd if=/dev/zero bs=32 count=1 2>/dev/null\n"

// g02aTongsuo builds the tongsuo backend exactly the way main.go/cli.go do.
func g02aTongsuo(t *testing.T, helper string, timeout time.Duration) (Cipher, error) {
	t.Helper()
	return NewCipher(Config{
		Backend:        "tongsuo",
		HelperPath:     helper,
		SubprocTimeout: timeout,
		MaxVolumeBytes: 1 << 30,
	})
}

var (
	g02aKey   = bytes.Repeat([]byte{0x11}, DekLen)
	g02aNonce = bytes.Repeat([]byte{0x22}, NonceLen)
	g02aPlain = []byte("0123456789abcdef")
)

// TestA176_SubSecondTimeoutStillServes: a 200 ms budget is a legitimate
// operator choice. It must degrade the budget, never the driver.
func TestA176_SubSecondTimeoutStillServes(t *testing.T) {
	helper := g02aHelperScript(t, g02aHelperEcho)
	c, err := g02aTongsuo(t, helper, 200*time.Millisecond)
	if err != nil {
		t.Fatalf("NewCipher(tongsuo, 200ms): %v", err)
	}
	ct, tag, err := c.Seal(g02aKey, g02aNonce, []byte("aad"), g02aPlain)
	if err != nil {
		t.Fatalf("Seal with a 200ms helper budget failed: %v — the millisecond config was truncated "+
			"to 0 seconds, so time.After(0) fires before the helper runs and every volume operation "+
			"is reported as a helper timeout (A176)", err)
	}
	if len(ct) != len(g02aPlain) || len(tag) != TagLen {
		t.Fatalf("unexpected helper output split: ct=%d tag=%d", len(ct), len(tag))
	}
}

// TestA176_NonPositiveTimeoutRefusedAtBoot: 0/negative can never bound a helper
// call. Refuse at construction (boot) instead of failing every request.
func TestA176_NonPositiveTimeoutRefusedAtBoot(t *testing.T) {
	helper := g02aHelperScript(t, g02aHelperEcho)
	for _, d := range []time.Duration{0, -time.Second, -500 * time.Millisecond} {
		c, err := g02aTongsuo(t, helper, d)
		if err == nil {
			t.Fatalf("SubprocTimeout=%s was accepted and produced %T: a non-positive budget makes "+
				"every helper call time out instantly, so the plugin must refuse to boot with it (A176)", d, c)
		}
		if !strings.Contains(err.Error(), "CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS") {
			t.Fatalf("SubprocTimeout=%s rejection must name the env key the operator has to fix, got: %v", d, err)
		}
	}
}

// TestA176_TimeoutErrorNamesTheConfiguredBudget: when a real timeout happens the
// error must report the budget that actually expired and the value configured,
// not a truncated integer that sends the operator to the wrong component.
func TestA176_TimeoutErrorNamesTheConfiguredBudget(t *testing.T) {
	// exec(2) so the Kill lands on the sleeper itself and this test isolates the
	// message, not the reaping behaviour (that is A178's subject).
	helper := g02aHelperScript(t, "exec sleep 5")
	c, err := g02aTongsuo(t, helper, time.Millisecond)
	if err != nil {
		t.Fatalf("NewCipher(tongsuo, 1ms): %v", err)
	}
	_, _, serr := c.Seal(g02aKey, g02aNonce, nil, g02aPlain)
	if serr == nil {
		t.Fatal("a helper that produced no output must not report success")
	}
	msg := serr.Error()
	if !strings.Contains(msg, "timeout") {
		t.Fatalf("expected a timeout error, got: %s", msg)
	}
	if strings.Contains(msg, "after 0s") {
		t.Fatalf("error reports a 0s budget — the millisecond config was truncated to seconds: %s", msg)
	}
	if !strings.Contains(msg, "1ms") {
		t.Fatalf("error must disclose the configured budget (1ms) so the truncation cannot be blamed on "+
			"Tongsuo performance, got: %s", msg)
	}
	const floor = "1s"
	if !strings.Contains(msg, floor) {
		t.Fatalf("error must also name the effective %s floor that actually expired, got: %s", floor, msg)
	}
}
