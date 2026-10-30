package main

// A205① (server side): Serve() declared "socket root:cds 0600" but treated
// chmod/chown as best-effort WARNs, so an image that runs the plugin as a
// non-root user served happily while owning nothing it later chowns or loop
// mounts.

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	volumepluginv1 "cds-volume-plugin/gen/volumepluginv1"
)

func a205Config(t *testing.T) Config {
	t.Helper()
	dir := t.TempDir()
	return Config{
		SocketPath:     filepath.Join(dir, "cds.sock"),
		VolumeRoot:     filepath.Join(dir, "volumes"),
		Backend:        "aes_fallback",
		SubprocTimeout: 10 * time.Second,
		MaxVolumeBytes: 1 << 30,
	}
}

func a205Plugin(t *testing.T) *Plugin {
	t.Helper()
	kms, err := NewKMSClient(a197MockKMS(t).URL, "a205-token")
	if err != nil {
		t.Fatalf("kms: %v", err)
	}
	return NewPlugin(a205Config(t), NewAESGCMCipher(), kms)
}

// TestA205_ServeRefusesToServeWithoutRootOwnership: as an unprivileged user the
// plugin cannot own its socket, cannot chown staged plaintext and cannot
// loop-mount, so booting is a silent promise it will break at the first Attach.
func TestA205_ServeRefusesToServeWithoutRootOwnership(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("this process is root: the ownership it must refuse here is exactly what it can do")
	}
	cfg := a205Config(t)
	// Served-instead-of-refused shows up as Serve() blocking forever, so the refusal
	// is awaited with a deadline: a regression must read as a failure, not a hang.
	done := make(chan error, 1)
	go func() { done <- Serve(cfg, a205Plugin(t)) }()
	var err error
	select {
	case err = <-done:
	case <-time.After(5 * time.Second):
		t.Fatalf("Serve() is still serving after 5s as uid-%d: it accepted a plugin that cannot own %s "+
			"or loop-mount anything; at HEAD this printed two WARN lines and served (A205①)",
			os.Geteuid(), cfg.SocketPath)
	}
	if !strings.Contains(err.Error(), EnvAllowNonRoot) {
		t.Fatalf("the refusal must name the opt-in the operator can set (%s), got: %v", EnvAllowNonRoot, err)
	}
	if !strings.Contains(err.Error(), "loop-mount") {
		t.Fatalf("the refusal must name the capability that is actually lost, got: %v", err)
	}
}

// TestA205_OptInServesAndTheSocketModeIsVerified: the opt-in keeps dev usable, and
// the mode the spec declares must be a verified fact on the way through.
func TestA205_OptInServesAndTheSocketModeIsVerified(t *testing.T) {
	cfg := a205Config(t)
	cfg.AllowNonRoot = true
	if err := os.WriteFile(cfg.SocketPath, nil, 0o666); err != nil {
		t.Fatalf("plant a wide-mode file at the socket path: %v", err)
	}

	serveErr := make(chan error, 1)
	go func() { serveErr <- Serve(cfg, a205Plugin(t)) }()
	t.Cleanup(func() {
		select {
		case err := <-serveErr:
			if err != nil {
				t.Logf("Serve returned %v", err)
			}
		default:
		}
	})

	deadline := time.Now().Add(10 * time.Second)
	for {
		if fi, err := os.Stat(cfg.SocketPath); err == nil && fi.Mode()&os.ModeSocket != 0 {
			break
		}
		select {
		case err := <-serveErr:
			t.Fatalf("Serve exited early with the opt-in set: %v", err)
		default:
		}
		if time.Now().After(deadline) {
			t.Fatalf("socket %s never appeared", cfg.SocketPath)
		}
		time.Sleep(5 * time.Millisecond)
	}

	fi, err := os.Stat(cfg.SocketPath)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Fatalf("served with socket mode %04o while declaring 0600 (A205①)", fi.Mode().Perm())
	}

	conn, err := grpc.NewClient("unix://"+cfg.SocketPath, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if _, err := volumepluginv1.NewVolumeControllerServiceClient(conn).Create(ctx,
		&volumepluginv1.CreateRequest{VolumeId: "a205-opt-in", Name: "a205-opt-in"}); err != nil {
		t.Fatalf("the opted-in server must still serve: %v", err)
	}
}

// TestA205_KMSServiceTokenIsRequiredAtBoot: the default is empty and the client
// simply omits the Authorization header, so serve would come up and 401 at the
// first DEK call.
func TestA205_KMSServiceTokenIsRequiredAtBoot(t *testing.T) {
	cfg := a204BaseConfig()
	cfg.KMSToken = ""
	err := cfg.Validate()
	if err == nil {
		t.Fatal("Validate() accepted a boot with no KMS credential: every DEK call would be sent unauthenticated and answered 401 (A205③)")
	}
	if !strings.Contains(err.Error(), EnvKMSToken) {
		t.Fatalf("the refusal must name %s, got: %v", EnvKMSToken, err)
	}

	cfg.KMSToken = "   "
	if err := cfg.Validate(); err == nil {
		t.Fatal("a whitespace-only token was accepted as a credential")
	}

	cfg.KMSToken = "a205-token"
	if err := cfg.Validate(); err != nil {
		t.Fatalf("a configured token must still boot: %v", err)
	}
}

// TestA205_EnforceSocketAccessTightensAndVerifies pins the unit: whatever mode the
// socket was created with, the function either lands it on 0600 or refuses.
func TestA205_EnforceSocketAccessTightensAndVerifies(t *testing.T) {
	cfg := a205Config(t)
	cfg.AllowNonRoot = true
	lis, err := net.Listen("unix", cfg.SocketPath)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer lis.Close()
	if err := os.Chmod(cfg.SocketPath, 0o666); err != nil {
		t.Fatalf("widen: %v", err)
	}
	if err := enforceSocketAccess(cfg); err != nil {
		t.Fatalf("enforceSocketAccess: %v", err)
	}
	fi, err := os.Stat(cfg.SocketPath)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Fatalf("socket left at %04o after enforcement", fi.Mode().Perm())
	}
}
