package main

// A197 second half: the same bare grpc.NewServer() had no recovery interceptor
// and no per-volume serialisation, so a panic in one handler escaped into the
// transport (default grpc behaviour: the process dies, stranding every mounted
// volume on this node) and two RPCs naming the same volume interleaved across the
// ledger/manifest read-modify-write.

import (
	"context"
	"hash/fnv"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	cdsvolumev1 "cds-volume-plugin/gen/cdsvolumev1"
)

type a197PanicSvc struct{}

func (a197PanicSvc) Boom(context.Context, *cdsvolumev1.GetVolumeInfoRequest) (*cdsvolumev1.GetVolumeInfoResponse, error) {
	panic("a197 induced handler panic")
}

// a197PanicDesc is spelled like protoc-gen-go output so the panic travels through
// the server's unary interceptor chain exactly as a real method's would.
var a197PanicDesc = grpc.ServiceDesc{
	ServiceName: "cds.test.PanicService",
	HandlerType: (*any)(nil),
	Methods: []grpc.MethodDesc{{
		MethodName: "Boom",
		Handler: func(srv any, ctx context.Context, dec func(any) error, interceptor grpc.UnaryServerInterceptor) (any, error) {
			var in cdsvolumev1.GetVolumeInfoRequest
			if err := dec(&in); err != nil {
				return nil, err
			}
			if interceptor == nil {
				return srv.(a197PanicSvc).Boom(ctx, &in)
			}
			info := &grpc.UnaryServerInfo{Server: srv, FullMethod: "/cds.test.PanicService/Boom"}
			handler := func(ctx context.Context, req any) (any, error) {
				return srv.(a197PanicSvc).Boom(ctx, req.(*cdsvolumev1.GetVolumeInfoRequest))
			}
			return interceptor(ctx, &in, info, handler)
		},
	}},
	Metadata: "a197_test",
}

func a197KMSClient(t *testing.T) *KMSClient {
	t.Helper()
	k, err := NewKMSClient(a197MockKMS(t).URL, "a197-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}
	return k
}

// TestA197_PanicBecomesInternalAndTheServerSurvives: the panic must come back as
// Internal on that one call, and the next call on the SAME connection must still
// be served.
func TestA197_PanicBecomesInternalAndTheServerSurvives(t *testing.T) {
	dir := t.TempDir()
	cfg := Config{
		SocketPath:     filepath.Join(dir, "cds.sock"),
		VolumeRoot:     filepath.Join(dir, "volumes"),
		Backend:        "aes_fallback",
		SubprocTimeout: 10 * time.Second,
		MaxVolumeBytes: 1 << 30,
		AllowNonRoot:   true,
	}
	srv := newGRPCServer(cfg, NewPlugin(cfg, NewAESGCMCipher(), a197KMSClient(t)))
	srv.RegisterService(&a197PanicDesc, a197PanicSvc{})

	lis, err := net.Listen("unix", cfg.SocketPath)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	go func() { _ = srv.Serve(lis) }()
	t.Cleanup(srv.Stop)

	conn, err := grpc.NewClient("unix://"+cfg.SocketPath, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	err = conn.Invoke(ctx, "/cds.test.PanicService/Boom",
		&cdsvolumev1.GetVolumeInfoRequest{VolumeId: "a197-panic"}, &cdsvolumev1.GetVolumeInfoResponse{})
	if status.Code(err) != codes.Internal {
		t.Fatalf("a handler panic must surface as INTERNAL on that call, got code %s: %v", status.Code(err), err)
	}
	if strings.Contains(err.Error(), "a197 induced handler panic") {
		t.Fatalf("the panic text (which can carry paths) must stay in the plugin log, not reach the client: %v", err)
	}

	if _, err := cdsvolumev1.NewCDSVolumeExtensionClient(conn).GetVolumeInfo(ctx,
		&cdsvolumev1.GetVolumeInfoRequest{VolumeId: "a197-panic"}); status.Code(err) == codes.Unavailable {
		t.Fatalf("the server died with the panic (connection unusable): %v", err)
	}
}

func a197Shard(volumeID string) int {
	h := fnv.New32a()
	_, _ = h.Write([]byte(volumeID))
	return int(h.Sum32() % volumeLockShards)
}

// TestA197_PerVolumeLockIsHeldThenReleased pins the serialisation primitive and
// that the volume-taking handlers actually take it — a lock only tests call would
// be worthless.
func TestA197_PerVolumeLockIsHeldThenReleased(t *testing.T) {
	const id = "cds-data-a197-lock"
	shard := a197Shard(id)

	unlock := volumeLocks.lock(id)
	if volumeLocks.shards[shard].TryLock() {
		unlock()
		t.Fatal("the per-volume lock for volume_id did not hold its shard")
	}
	unlock()
	if !volumeLocks.shards[shard].TryLock() {
		t.Fatal("the per-volume lock was not released after unlock")
	}
	volumeLocks.shards[shard].Unlock()

	src, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatalf("read server.go: %v", err)
	}
	if got := a197CountHandlerLocks(string(src)); got < 6 {
		t.Fatalf("only %d of the volume-taking RPC handlers serialise on volume_id (want >= 6): "+
			"concurrent Create/Put/Attach/Detach/Destroy/Info interleave the manifest and the ledger (A197)", got)
	}
}

func a197CountHandlerLocks(src string) int {
	n := 0
	inHandler := false
	for _, line := range strings.Split(src, "\n") {
		switch {
		case strings.HasPrefix(line, "func (s *"), strings.HasPrefix(line, "func (c *"):
			inHandler = true
		case strings.HasPrefix(line, "}"):
			inHandler = false
		case inHandler && strings.Contains(line, "volumeLocks.lock("):
			n++
		}
	}
	return n
}
