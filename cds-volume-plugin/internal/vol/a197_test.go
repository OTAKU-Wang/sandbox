package vol_test

import . "cds-volume-plugin/internal/vol"

// A197 (OCR 2026-10-18, high): the gRPC server was constructed with a bare
// grpc.NewServer(), so grpc-go's DEFAULT 4 MiB receive cap sits underneath the
// plugin's own volume cap (config.go MaxVolumeBytes defaults to 1 GiB). Every
// PutVolumeData / ReadSandboxFile above 4 MiB therefore dies in the transport
// layer and reaches Cubelet as an unrelated error, while the plugin claims it
// accepts 1 GiB volumes.
//
// These tests drive the PRODUCTION Serve() over a real unix socket with a real
// gRPC client — no in-process shortcut — because the defect lives in the
// transport options Serve() passes to grpc.NewServer().

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	cdsvolumev1 "cds-volume-plugin/gen/cdsvolumev1"
	volumepluginv1 "cds-volume-plugin/gen/volumepluginv1"
)

// ── self-contained stand-in for the control-plane KMS ──────────────────────

type a197KMS struct {
	mu   sync.Mutex
	refs map[string]string
	n    int
}

func a197MockKMS(t *testing.T) *httptest.Server {
	t.Helper()
	k := &a197KMS{refs: map[string]string{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		k.mu.Lock()
		defer k.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/kms/volume-keys":
			var body struct {
				DEKHex string `json:"dek_hex"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			k.n++
			ref := "a197-ref-" + itoa(k.n)
			k.refs[ref] = body.DEKHex
			_ = json.NewEncoder(w).Encode(map[string]string{"key_id": ref})
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/distribute"):
			ref := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/v1/kms/volume-keys/"), "/distribute")
			dek, ok := k.refs[ref]
			if !ok {
				http.Error(w, "unknown ref", http.StatusNotFound)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]string{"key_hex": dek})
		case r.Method == http.MethodDelete:
			w.WriteHeader(http.StatusNoContent)
		default:
			http.Error(w, "unexpected", http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// a197Serve starts the production Serve() on a temp unix socket and returns a
// connected client for every service the plugin registers.
func a197Serve(t *testing.T, maxVolumeBytes int64) (volumepluginv1.VolumeControllerServiceClient, cdsvolumev1.CDSVolumeExtensionClient) {
	t.Helper()
	dir := t.TempDir()
	cfg := Config{
		SocketPath:     dir + "/cds.sock",
		VolumeRoot:     dir + "/volumes",
		Backend:        "aes_fallback",
		SubprocTimeout: 10 * time.Second,
		MaxVolumeBytes: maxVolumeBytes,
		AllowNonRoot:   true,
	}
	c := NewAESGCMCipher()
	if err := RunSelfTest(c); err != nil {
		t.Fatalf("self-test: %v", err)
	}
	kms, err := NewKMSClient(a197MockKMS(t).URL, "a197-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}
	p := NewPlugin(cfg, c, kms)

	serveErr := make(chan error, 1)
	go func() { serveErr <- Serve(cfg, p) }()
	t.Cleanup(func() {
		select {
		case err := <-serveErr:
			if err != nil && !strings.Contains(err.Error(), "use of closed") {
				t.Logf("Serve returned %v", err)
			}
		default:
		}
	})

	// Wait for the socket to exist (bounded), then dial. Polling for the listen
	// socket — not a sleep — is the readiness signal Serve() publishes.
	deadline := time.Now().Add(10 * time.Second)
	for {
		if fi, err := os.Stat(cfg.SocketPath); err == nil && fi.Mode()&os.ModeSocket != 0 {
			break
		}
		select {
		case err := <-serveErr:
			t.Fatalf("Serve exited early: %v", err)
		default:
		}
		if time.Now().After(deadline) {
			t.Fatalf("socket %s never appeared", cfg.SocketPath)
		}
		time.Sleep(5 * time.Millisecond)
	}

	conn, err := grpc.NewClient("unix://"+cfg.SocketPath, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return volumepluginv1.NewVolumeControllerServiceClient(conn),
		cdsvolumev1.NewCDSVolumeExtensionClient(conn)
}

// a197Payload returns deterministic pseudo-random bytes (compressing them would
// hide a transport cap that only bites on the wire size).
func a197Payload(n int) []byte {
	b := make([]byte, n)
	x := uint32(0x12345678)
	for i := range b {
		x = x*1664525 + 1013904223
		b[i] = byte(x >> 24)
	}
	return b
}

// TestA197_DataLargerThanGRPCDefaultLimitRoundTrips is the reading the finding
// predicts: with MaxVolumeBytes = 1 GiB, a 5 MiB volume payload must cross the
// socket. With the bare grpc.NewServer() the server answers RESOURCE_EXHAUSTED
// ("received message larger than max (… vs. 4194304)") — the volume is nowhere
// near its configured cap.
func TestA197_DataLargerThanGRPCDefaultLimitRoundTrips(t *testing.T) {
	ctrl, ext := a197Serve(t, 1<<30)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	if _, err := ctrl.Create(ctx, &volumepluginv1.CreateRequest{VolumeId: "a197-big", Name: "a197-big"}); err != nil {
		t.Fatalf("Create: %v", err)
	}
	payload := a197Payload(5 << 20) // 5 MiB > grpc-go's 4 MiB default receive cap
	resp, err := ext.PutVolumeData(ctx, &cdsvolumev1.PutVolumeDataRequest{
		VolumeId: "a197-big", Aad: "a197-big", Data: payload,
	})
	if err != nil {
		t.Fatalf("PutVolumeData(5 MiB) under a 1 GiB MaxVolumeBytes failed with code %s: %v — "+
			"the transport cap, not the volume cap, is what refused it (A197)", status.Code(err), err)
	}
	if resp.GetDataSizeBytes() != uint64(len(payload)) {
		t.Fatalf("sealed %d bytes, manifest reports %d", len(payload), resp.GetDataSizeBytes())
	}
	info, err := ext.GetVolumeInfo(ctx, &cdsvolumev1.GetVolumeInfoRequest{VolumeId: "a197-big"})
	if err != nil || !info.GetHasData() || info.GetDataSizeBytes() != uint64(len(payload)) {
		t.Fatalf("GetVolumeInfo after a 5 MiB put: info=%+v err=%v", info, err)
	}
}

// TestA197_TransportCapFollowsMaxVolumeBytes is the counterpart lock: raising the
// transport cap must not turn the plugin into an unbounded allocator. The wire
// limit has to track CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES, so with a 1 MiB volume
// cap an 8 MiB message is still refused and a 512 KiB one still lands.
func TestA197_TransportCapFollowsMaxVolumeBytes(t *testing.T) {
	ctrl, ext := a197Serve(t, 1<<20)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	if _, err := ctrl.Create(ctx, &volumepluginv1.CreateRequest{VolumeId: "a197-small", Name: "a197-small"}); err != nil {
		t.Fatalf("Create: %v", err)
	}
	if _, err := ext.PutVolumeData(ctx, &cdsvolumev1.PutVolumeDataRequest{
		VolumeId: "a197-small", Aad: "a197-small", Data: a197Payload(512 << 10),
	}); err != nil {
		t.Fatalf("512 KiB under a 1 MiB volume cap must be accepted, got code %s: %v", status.Code(err), err)
	}
	_, err := ext.PutVolumeData(ctx, &cdsvolumev1.PutVolumeDataRequest{
		VolumeId: "a197-small", Aad: "a197-small", Data: a197Payload(8 << 20),
	})
	if err == nil {
		t.Fatal("an 8 MiB message was accepted under a 1 MiB MaxVolumeBytes: the transport cap is not derived from the config (A197)")
	}
	if status.Code(err) != codes.ResourceExhausted {
		t.Fatalf("oversized message must be refused at the transport with RESOURCE_EXHAUSTED, got code %s: %v", status.Code(err), err)
	}
}
