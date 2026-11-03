package vol_test

import . "cds-volume-plugin/internal/vol"

// A199 legs the parent routed to this track: the RPC layer used to answer every
// failure below it with codes.Internal (a 500 the Cubelet runtime retries), and
// rootfs.go's id guard let glob metacharacters through into
// filepath.Glob("sb-<id>-cds-rootfs-*").

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	cdsvolumev1 "cds-volume-plugin/gen/cdsvolumev1"
)

func a199bPlugin(t *testing.T, rootfsRoot string) *Plugin {
	t.Helper()
	dir := t.TempDir()
	if rootfsRoot != "" {
		t.Setenv("CDS_VOLUME_PLUGIN_ROOTFS_VOLUME_ROOT", rootfsRoot)
	}
	cfg := Config{
		VolumeRoot:     filepath.Join(dir, "volumes"),
		Backend:        "aes_fallback",
		SubprocTimeout: 10 * time.Second,
		MaxVolumeBytes: 1 << 30,
	}
	kms, err := NewKMSClient(a197MockKMS(t).URL, "a199b-token")
	if err != nil {
		t.Fatalf("kms: %v", err)
	}
	return NewPlugin(cfg, NewAESGCMCipher(), kms)
}

// a199bForeignDir plants another tenant's rootfs volume directory and returns it.
func a199bForeignDir(t *testing.T, root, cubebox, session string) string {
	t.Helper()
	dir := filepath.Join(root, "sb-"+cubebox+"-cds-rootfs-"+session)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatalf("plant: %v", err)
	}
	return dir
}

// TestA199Rootfs_GlobMetacharactersCannotReachAnotherSandbox is the rootfs leg:
// "*" (or "?"/"[") in cubebox_id is interpolated into the volume-directory glob,
// so at HEAD it resolved to whichever rootfs volume matched first — another
// tenant's image — instead of refusing.
func TestA199Rootfs_GlobMetacharactersCannotReachAnotherSandbox(t *testing.T) {
	root := t.TempDir()
	foreign := a199bForeignDir(t, root, "0f1e2d3c4b5a69788796a5b4c3d2e1f0", "5b3e1f0a-0000-0000-0000-000000000001")
	p := a199bPlugin(t, root)
	ctx := context.Background()
	_ = ctx

	for _, hostile := range []string{"*", "?", "0f1e2d3c4b5a69788796a5b4c3d2e1f?", "[0-9a-f]*", "*-cds-rootfs-*"} {
		_, _, err := p.ReadSandboxFile(hostile, "", "out/result.csv")
		if err == nil {
			t.Fatalf("ReadSandboxFile(cubebox_id=%q) accepted a glob metacharacter", hostile)
		}
		if !strings.HasPrefix(err.Error(), "invalid sandbox id") {
			t.Fatalf("cubebox_id=%q must be refused by the id guard, got: %v", hostile, err)
		}
		if strings.Contains(err.Error(), foreign) || strings.Contains(err.Error(), hostile) {
			t.Fatalf("refusal for %q must not echo the id nor the foreign volume path it reached: %v", hostile, err)
		}
	}
}

// TestA199Rootfs_AmbiguousLookupIsRefusedNotFirstMatch: with two rootfs volumes
// on the node and no session_id, "whichever matched first" silently reads a
// different tenant than the one named.
func TestA199Rootfs_AmbiguousLookupIsRefusedNotFirstMatch(t *testing.T) {
	root := t.TempDir()
	a := a199bForeignDir(t, root, "0f1e2d3c4b5a69788796a5b4c3d2e1f0", "5b3e1f0a-0000-0000-0000-000000000001")
	b := a199bForeignDir(t, root, "0f1e2d3c4b5a69788796a5b4c3d2e1f0", "5b3e1f0a-0000-0000-0000-000000000002")
	p := a199bPlugin(t, root)

	_, _, err := p.ReadSandboxFile("0f1e2d3c4b5a69788796a5b4c3d2e1f0", "", "out/result.csv")
	if err == nil || !strings.Contains(err.Error(), "ambiguous") {
		t.Fatalf("an ambiguous rootfs lookup must be refused, got %v (HEAD picked matches[0] = %s or %s)", err, a, b)
	}
	if strings.Contains(err.Error(), a) || strings.Contains(err.Error(), b) {
		t.Fatalf("the ambiguity refusal must not name either tenant's directory: %v", err)
	}
}

// TestA199Rootfs_LegitimateIDsStillReachTheMountPath is the over-tightening guard:
// a 32-hex cubebox id plus a uuid session must still resolve and fail only on the
// missing image / missing mount privilege.
func TestA199Rootfs_LegitimateIDsStillReachTheMountPath(t *testing.T) {
	root := t.TempDir()
	p := a199bPlugin(t, root)
	_, _, err := p.ReadSandboxFile("0f1e2d3c4b5a69788796a5b4c3d2e1f0", "5b3e1f0a-0000-0000-0000-000000000001", "out/result.csv")
	if err == nil {
		t.Fatal("expected the missing rootfs directory to be reported")
	}
	if strings.HasPrefix(err.Error(), "invalid sandbox id") || strings.HasPrefix(err.Error(), "invalid session id") {
		t.Fatalf("legitimate cubebox/session ids were refused by the guard: %v", err)
	}
}

// TestA199Srv_ReadSandboxFileFailureIsNotABareInternal is the gRPC leg: the method
// answered every plugin-layer failure with codes.Internal, which Cubelet cannot
// act on. A missing rootfs volume is a precondition/absence fact, not an internal
// error.
func TestA199Srv_ReadSandboxFileFailureIsNotABareInternal(t *testing.T) {
	root := t.TempDir()
	p := a199bPlugin(t, root)
	ext := NewExtensionService(p)

	_, err := ext.ReadSandboxFile(context.Background(), &cdsvolumev1.ReadSandboxFileRequest{
		CubeboxId: "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
		SessionId: "5b3e1f0a-0000-0000-0000-000000000001",
		VmPath:    "out/result.csv",
	})
	if status.Code(err) == codes.Internal {
		t.Fatalf("a missing rootfs volume dir reached Cubelet as INTERNAL (a retryable-looking 500): %v", err)
	}
	if status.Code(err) != codes.NotFound {
		t.Fatalf("a missing rootfs volume dir must be NOT_FOUND, got %s: %v", status.Code(err), err)
	}
}

// TestA199Srv_MapErrClassifiesLayerRejections pins the classification of the
// rejection stems the layers under server.go actually produce, so a bad request is
// never reported as "volume in the wrong state".
func TestA199Srv_MapErrClassifiesLayerRejections(t *testing.T) {
	cases := []struct {
		Name string
		err  error
		want codes.Code
	}{
		{"volume id charset", errors.New("invalid volume_id: byte 3 is 0x2a, outside [A-Za-z0-9._-] (fail-closed)"), codes.InvalidArgument},
		{"containment", errors.New(`volume_id "x" does not resolve to one directory under the volume root (fail-closed)`), codes.InvalidArgument},
		{"sandbox id glob", errors.New("invalid sandbox id: contains a glob metacharacter, which would widen the rootfs volume lookup to another sandbox (fail-closed)"), codes.InvalidArgument},
		{"missing volume", ErrVolumeNotFound{ID: "cds-data-x"}, codes.NotFound},
		{"missing rootfs image", &forbiddenError{"rootfs image /var/lib/cds/volumes/sb-a-cds-rootfs-b/sb-a-cds-rootfs-b: no such file or directory"}, codes.NotFound},
		{"state", errors.New("volume cds-data-x already has data (idempotent retry must be byte-identical)"), codes.FailedPrecondition},
	}
	for _, tc := range cases {
		got := status.Code(MapErr(tc.err))
		if got != tc.want {
			t.Errorf("%s: MapErr gave %s, want %s (err %v)", tc.Name, got, tc.want, tc.err)
		}
	}
	if status.Code(MapErr(nil)) != codes.OK {
		t.Error("MapErr(nil) must stay OK")
	}
}

// forbiddenError is an os.Stat-shaped absence error without importing syscall here.
type forbiddenError struct{ msg string }

func (e *forbiddenError) Error() string { return e.msg }
func (e *forbiddenError) Is(target error) bool {
	return target == os.ErrNotExist && strings.HasSuffix(e.msg, "no such file or directory")
}
