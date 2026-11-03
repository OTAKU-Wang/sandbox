package vol_test

import . "cds-volume-plugin/internal/vol"

// A180 (OCR 2026-10-18, high) — do() discards the io.ReadAll error and treats
// every status < 300 as success, so RevokeDEK can report "key destroyed" for a
// control plane that answered something else entirely.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func a180Client(t *testing.T, h http.HandlerFunc) *KMSClient {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	c, err := NewKMSClient(srv.URL, "test-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}
	return c
}

// TestA180_RevokeAcceptsOnly204: the contract is DELETE …/volume-keys/{ref} ->
// 204. A 200 whose body is an HTML page (proxy, login wall, error page rendered
// with a 2xx) must never be read as "the DEK is destroyed".
func TestA180_RevokeAcceptsOnly204(t *testing.T) {
	c := a180Client(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write([]byte("<html><body>200 OK — cache gateway</body></html>"))
	})

	err := c.RevokeDEK(context.Background(), "vkey-00000000000000000000000000000001")
	if err == nil {
		t.Fatalf("RevokeDEK reported success for HTTP 200 with an HTML body; the contract is 204. " +
			"Destroy would be reported (and the local key material erased) with the DEK still ACTIVE at the control plane.")
	}
	if !strings.Contains(err.Error(), "204") {
		t.Errorf("error must name the expected status, got: %v", err)
	}
}

// TestA180_BodyReadFailureIsFailure: the response body read error is dropped on
// the floor today, so a truncated 2xx reply becomes a success.
func TestA180_BodyReadFailureIsFailure(t *testing.T) {
	c := a180Client(t, func(w http.ResponseWriter, r *http.Request) {
		hj, ok := w.(http.Hijacker)
		if !ok {
			t.Errorf("test server cannot hijack")
			return
		}
		conn, _, err := hj.Hijack()
		if err != nil {
			t.Errorf("hijack: %v", err)
			return
		}
		defer conn.Close()
		_, _ = conn.Write([]byte("HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\ntruncated"))
	})

	err := c.RevokeDEK(context.Background(), "vkey-00000000000000000000000000000001")
	if err == nil {
		t.Fatalf("RevokeDEK reported success although the response body could not be read to the end")
	}
	if !strings.Contains(err.Error(), "body") {
		t.Errorf("error must name the failed body read, got: %v", err)
	}
}

// TestA180_KeyBearingCallsRequireBody: register/distribute reply WITH a body —
// an empty 2xx is the clearest possible failure and must be reported as one.
func TestA180_KeyBearingCallsRequireBody(t *testing.T) {
	for _, tc := range []struct {
		Name string
		call func(*KMSClient) error
	}{
		{"register", func(c *KMSClient) error {
			_, err := c.RegisterDEK(context.Background(), "vol-1", "cds-data-vol-1", "aes-128-gcm(fallback,non-GM)", make([]byte, DekLen))
			return err
		}},
		{"unwrap", func(c *KMSClient) error {
			_, err := c.UnwrapDEK(context.Background(), "vkey-00000000000000000000000000000001", "session-1")
			return err
		}},
	} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) }))
		c, err := NewKMSClient(srv.URL, "test-token")
		if err != nil {
			srv.Close()
			t.Fatalf("kms client: %v", err)
		}
		err = tc.call(c)
		srv.Close()
		if err == nil {
			t.Fatalf("%s: an empty 2xx body was accepted", tc.Name)
		}
		if !strings.Contains(err.Error(), "empty response body") {
			t.Errorf("%s: error must name the empty response body, got: %v", tc.Name, err)
		}
	}
}
