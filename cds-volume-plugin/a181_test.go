package main

// A181 (OCR 2026-10-18, medium-high) — the KMS ref is concatenated into the URL
// path unvalidated, so a ref that is not a bare segment moves the request to
// another resource; /volume-keys/{ref}/distribute is the key-releasing route.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type a181Recorder struct {
	srv   *httptest.Server
	paths chan string
}

func a181Server(t *testing.T, reply func(w http.ResponseWriter, path string)) *a181Recorder {
	t.Helper()
	rec := &a181Recorder{paths: make(chan string, 16)}
	rec.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec.paths <- r.URL.RequestURI()
		reply(w, r.URL.RequestURI())
	}))
	t.Cleanup(rec.srv.Close)
	return rec
}

func (r *a181Recorder) client(t *testing.T) *KMSClient {
	t.Helper()
	c, err := NewKMSClient(r.srv.URL, "test-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}
	return c
}

func (r *a181Recorder) drained(t *testing.T) []string {
	t.Helper()
	var seen []string
	for {
		select {
		case p := <-r.paths:
			seen = append(seen, p)
			continue
		default:
			return seen
		}
	}
}

func a181OK(w http.ResponseWriter, path string) {
	if strings.HasSuffix(path, "/distribute") {
		_, _ = w.Write([]byte(`{"key_hex":"00112233445566778899aabbccddeeff"}`))
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func TestA181_HostileRefsNeverReachTheWire(t *testing.T) {
	refs := []string{
		"../../volume-keys/vkey-victim",
		"vkey-1/../../kms/admin",
		"vkey-1/..%2f..%2fadmin",
		"/absolute/path",
		"vkey-1?session=other",
		"vkey-1#frag",
		"vkey-1/extra",
		"%2e%2e%2fvkey-victim",
		"vkey\tX-Probe: 1",
		"",
		strings.Repeat("v", 129),
	}
	for _, ref := range refs {
		rec := a181Server(t, a181OK)
		c := rec.client(t)
		ctx := context.Background()

		if dek, err := c.UnwrapDEK(ctx, ref, "session-1"); err == nil {
			t.Fatalf("UnwrapDEK(%q) returned %d key bytes with no error", ref, len(dek))
		}
		if err := c.RevokeDEK(ctx, ref); err == nil {
			t.Fatalf("RevokeDEK(%q) reported success", ref)
		}
		if seen := rec.drained(t); len(seen) != 0 {
			t.Fatalf("ref %q reached the control plane as %v: a ref must be one bare path segment", ref, seen)
		}
	}
}

func TestA181_TraversalRefIsNamedAsARefRejection(t *testing.T) {
	rec := a181Server(t, a181OK)
	c := rec.client(t)
	_, err := c.UnwrapDEK(context.Background(), "../../volume-keys/vkey-victim", "session-1")
	if err == nil {
		t.Fatalf("traversal ref accepted")
	}
	if !strings.Contains(err.Error(), "refusing key ref") {
		t.Errorf("error must name the ref rejection, got: %v", err)
	}
	if strings.Contains(err.Error(), "vkey-victim") {
		t.Errorf("error must not echo the tainted ref, got: %v", err)
	}
}

func TestA181_RegisterRejectsPathShapedKeyID(t *testing.T) {
	rec := a181Server(t, func(w http.ResponseWriter, path string) {
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"key_id":"../../volume-keys/vkey-victim"}`))
	})
	c := rec.client(t)
	ref, err := c.RegisterDEK(context.Background(), "vol-1", "cds-data-vol-1",
		"aes-128-gcm(fallback,non-GM)", make([]byte, dekLen))
	if err == nil {
		t.Fatalf("RegisterDEK returned the control plane's path-shaped key_id %q as a usable ref", ref)
	}
	if ref != "" {
		t.Fatalf("RegisterDEK returned ref %q alongside the error", ref)
	}
}

func TestA181_LegitimateRefStillWorks(t *testing.T) {
	rec := a181Server(t, a181OK)
	c := rec.client(t)
	ctx := context.Background()
	const ref = "vkey-0f1e2d3c4b5a69788796a5b4c3d2e1f0"

	dek, err := c.UnwrapDEK(ctx, ref, "session-1")
	if err != nil {
		t.Fatalf("UnwrapDEK(%q): %v", ref, err)
	}
	if len(dek) != dekLen {
		t.Fatalf("unwrap returned %d key bytes, want %d", len(dek), dekLen)
	}
	if err := c.RevokeDEK(ctx, ref); err != nil {
		t.Fatalf("RevokeDEK(%q): %v", ref, err)
	}
	want := "/api/v1/kms/volume-keys/" + ref
	for _, p := range rec.drained(t) {
		if p != want && p != want+"/distribute" {
			t.Fatalf("legitimate ref was sent to %q, want %q", p, want)
		}
	}
}
