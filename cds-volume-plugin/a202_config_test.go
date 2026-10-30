package main

// A202③ canonical leg (parent-routed): Config.Validate() never looked at
// SubprocTimeout, so boot passed the KMS/transport checks and only failed later as
// a crypto/self-test error that blames the Tongsuo helper. The value-level rule
// already existed in NewCipher; Validate() now calls that same predicate instead of
// restating a threshold.

import (
	"strings"
	"testing"
	"time"
)

func a202cConfig(timeout time.Duration) Config {
	cfg := a204BaseConfig()
	cfg.Backend = "tongsuo"
	cfg.HelperPath = "/nonexistent/sm4_gcm"
	cfg.SubprocTimeout = timeout
	return cfg
}

func TestA202Config_ValidateRefusesUnusableHelperBudget(t *testing.T) {
	for _, bad := range []time.Duration{0, -5 * time.Second} {
		err := a202cConfig(bad).Validate()
		if err == nil {
			t.Fatalf("Validate() accepted a helper budget of %s: boot would continue and every Seal/Open would fail as a crypto error (A202③)", bad)
		}
		if !strings.Contains(err.Error(), "CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS") {
			t.Fatalf("the refusal must name the env key the operator has to fix, got: %v", err)
		}
		if want := checkHelperBudget(bad).Error(); err.Error() != want {
			t.Fatalf("Validate must delegate to the canonical predicate rather than restate it:\n got %q\nwant %q", err.Error(), want)
		}
	}
}

// TestA202Config_SubSecondBudgetStaysLegal is the A176 counterpart lock: a 200 ms
// budget is a legitimate operator choice (raised to the floor with a WARNING by the
// cipher), so Validate must not report it as a config error.
func TestA202Config_SubSecondBudgetStaysLegal(t *testing.T) {
	if err := a202cConfig(200 * time.Millisecond).Validate(); err != nil {
		t.Fatalf("a 200ms helper budget was refused as a config error: %v", err)
	}
	if err := a204BaseConfig().Validate(); err != nil {
		t.Fatalf("the aes_fallback default config must stay valid: %v", err)
	}
}
