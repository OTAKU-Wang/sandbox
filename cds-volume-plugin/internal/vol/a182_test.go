package vol_test

import . "cds-volume-plugin/internal/vol"

// A182 (OCR 2026-10-18, medium) — the raw upstream body (up to 1 MiB) is spliced
// into the error string, which reaches the plugin log verbatim; on the register
// call the same echo can carry the DEK the request just sent.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func a182Client(t *testing.T, h http.HandlerFunc) *KMSClient {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	c, err := NewKMSClient(srv.URL, "test-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}
	return c
}

func TestA182_ErrorBodyIsABoundedDigest(t *testing.T) {
	filler := strings.Repeat("<div class='gateway-error'>", 400) // ~10.8 kB of markup
	head := "upstream gateway blew up "
	forged := "\r\n2026-10-19 00:00:00 INFO plugin: crypto-erase completed\r\n"
	c := a182Client(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(forged + head + filler))
	})

	err := c.RevokeDEK(context.Background(), "vkey-0f1e2d3c4b5a69788796a5b4c3d2e1f0")
	if err == nil {
		t.Fatalf("a 502 reply was accepted")
	}
	msg := err.Error()
	if !strings.Contains(msg, "HTTP 502") {
		t.Errorf("error must name the status, got: %s", msg)
	}
	if !strings.Contains(msg, head) {
		t.Errorf("error must keep the leading diagnostic text, got: %s", msg)
	}
	if strings.Contains(msg, filler) {
		t.Fatalf("error spliced the whole %d-byte upstream body into the log line (%d bytes of message)",
			len(filler), len(msg))
	}
	if !strings.Contains(msg, "more bytes") {
		t.Errorf("error must disclose that the body was truncated, got: %s", msg)
	}
	if len(msg) > 600 {
		t.Errorf("error line is %d bytes, want a bounded summary (<600): %.200s…", len(msg), msg)
	}
	for i := 0; i < len(msg); i++ {
		if b := msg[i]; b < 0x20 || b == 0x7f {
			t.Fatalf("error leaked control byte %#x at offset %d into a single-line log field: %q", b, i, msg)
		}
	}
	if strings.Contains(msg, "\r\n") {
		t.Errorf("a body can inject a second log line into the message: %q", msg)
	}
}

func TestA182_RegisterErrorNeverEchoesKeyMaterial(t *testing.T) {
	dek := []byte{0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef,
		0xfe, 0xdc, 0xba, 0x98, 0x76, 0x54, 0x32, 0x10}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = w.Write([]byte(`{"detail":"dek_hex failed validation: 0123456789abcdef` +
			`fedcba9876543210 (rejected)"}`))
	}))
	t.Cleanup(srv.Close)
	c, err := NewKMSClient(srv.URL, "test-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}

	_, err = c.RegisterDEK(context.Background(), "vol-1", "cds-data-vol-1",
		"aes-128-gcm(fallback,non-GM)", dek)
	if err == nil {
		t.Fatalf("a 422 reply was accepted")
	}
	msg := err.Error()
	if strings.Contains(msg, "0123456789abcdef") || strings.Contains(msg, "dek_hex") {
		t.Fatalf("the register error echoed request/response key material into the log: %s", msg)
	}
	if !strings.Contains(msg, "HTTP 422") {
		t.Errorf("error must still name the status, got: %s", msg)
	}
}
