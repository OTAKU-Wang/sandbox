package vol_test

import . "cds-volume-plugin/internal/vol"

// A199 (server half, OCR 2026-10-18, high) — the gRPC boundary checked almost
// nothing: Create/PutVolumeData tested only for the EMPTY string, Attach only for
// an empty volume_id/volume_base_dir, and Detach/Destroy/GetVolumeInfo/PutVolumeData
// mapped no field at all, so `volume_id: "../../x"` reached paths() and came back
// as FailedPrecondition (a 500-class report) instead of InvalidArgument.
// plugin.go refuses hostile ids since the A199 plugin fix, but the transport
// independently has to (a) name the field, (b) answer InvalidArgument, (c) never
// echo attacker-controlled bytes into a client string or a plugin log line —
// volume_id is attacker-reachable, so a "\n" in it forges a log entry.
//
// Scope note: the rootfs.go half of A199 (guest-controlled symlink following,
// filepath.Glob first-match in cubeboxID) is NOT this file. The metacharacter/
// separator part is closed here at the RPC boundary; the rootfs internals belong to
// that file's owner.

import (
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	cdsvolumev1 "cds-volume-plugin/gen/cdsvolumev1"
	volumepluginv1 "cds-volume-plugin/gen/volumepluginv1"
)

// g02aKMS is the minimal control-plane stand-in for the three volume-key routes
// the plugin consumes (201 register / 200 distribute / 204 revoke, like
// app/api/kms.py). It records whether a rejected request leaked as far as the KMS.
type g02aKMS struct {
	mu       sync.Mutex
	Refs     []string
	Revoked  []string
	Requests []string
	Keys     map[string]string
}

func (k *g02aKMS) firstRef() string {
	k.mu.Lock()
	defer k.mu.Unlock()
	if len(k.Refs) > 0 {
		return k.Refs[0]
	}
	return ""
}

func (k *g02aKMS) counts() (requests int, revoked int) {
	k.mu.Lock()
	defer k.mu.Unlock()
	return len(k.Requests), len(k.Revoked)
}

func g02aMockKMS(t *testing.T) (*g02aKMS, *httptest.Server) {
	t.Helper()
	k := &g02aKMS{Keys: map[string]string{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		buf := new(bytes.Buffer)
		_, _ = buf.ReadFrom(r.Body)
		body := buf.Bytes()

		k.mu.Lock()
		defer k.mu.Unlock()
		k.Requests = append(k.Requests, r.Method+" "+r.URL.Path)
		w.Header().Set("Content-Type", "application/json")

		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/kms/volume-keys":
			var req struct {
				DEKHex string `json:"dek_hex"`
			}
			_ = json.Unmarshal(body, &req)
			ref := "vkey-" + strings.Repeat("0", 31-len(k.Refs)) + "1"
			k.Refs = append(k.Refs, ref)
			k.Keys[ref] = req.DEKHex
			_ = json.NewEncoder(w).Encode(map[string]string{"key_id": ref})
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/distribute"):
			ref := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/v1/kms/volume-keys/"), "/distribute")
			dek, ok := k.Keys[ref]
			if !ok {
				http.Error(w, "unknown ref", http.StatusNotFound)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]string{"key_hex": dek})
		case r.Method == http.MethodDelete && strings.HasPrefix(r.URL.Path, "/api/v1/kms/volume-keys/"):
			k.Revoked = append(k.Revoked, strings.TrimPrefix(r.URL.Path, "/api/v1/kms/volume-keys/"))
			w.WriteHeader(http.StatusNoContent)
		default:
			http.Error(w, "unexpected", http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return k, srv
}

func g02aPlugin(t *testing.T, root string, srv *httptest.Server) *Plugin {
	t.Helper()
	c := NewAESGCMCipher()
	if err := RunSelfTest(c); err != nil {
		t.Fatalf("self-test: %v", err)
	}
	kms, err := NewKMSClient(srv.URL, "test-token")
	if err != nil {
		t.Fatalf("kms client: %v", err)
	}
	return NewPlugin(Config{VolumeRoot: root, Backend: "aes_fallback", MaxVolumeBytes: 1 << 30}, c, kms)
}

func g02aPrivData(ref string) string {
	b, _ := json.Marshal(map[string]string{"session_dek_ref": ref})
	return string(b)
}

// g02aServices builds the three services the way Serve() does; one place spells
// the wiring out so the boundary tests follow the production construction.
func g02aServices(p *Plugin) (*NodeService, *ControllerService, *ExtensionService) {
	return NewNodeService(p), NewControllerService(p), NewExtensionService(p)
}

func g02aAttach(id, sandbox, baseDir, priv string, refCount int64) *volumepluginv1.AttachRequest {
	return &volumepluginv1.AttachRequest{
		VolumeId: id, SandboxId: sandbox, VolumeBaseDir: baseDir, PrivateData: priv, RefCount: refCount,
	}
}

func g02aDetach(id, sandbox string, refCount int64, meta map[string]string) *volumepluginv1.DetachRequest {
	return &volumepluginv1.DetachRequest{VolumeId: id, SandboxId: sandbox, RefCount: refCount, Metadata: meta}
}

func g02aCreate(id string) *volumepluginv1.CreateRequest {
	return &volumepluginv1.CreateRequest{VolumeId: id, Name: id}
}

func g02aDestroy(id string) *volumepluginv1.DestroyRequest {
	return &volumepluginv1.DestroyRequest{VolumeId: id}
}

func g02aPut(id string, data []byte) *cdsvolumev1.PutVolumeDataRequest {
	return &cdsvolumev1.PutVolumeDataRequest{VolumeId: id, Aad: id, Data: data}
}

func g02aInfo(id string) *cdsvolumev1.GetVolumeInfoRequest {
	return &cdsvolumev1.GetVolumeInfoRequest{VolumeId: id}
}

func g02aRead(cubebox, session, vmPath string) *cdsvolumev1.ReadSandboxFileRequest {
	return &cdsvolumev1.ReadSandboxFileRequest{CubeboxId: cubebox, SessionId: session, VmPath: vmPath}
}

// g02aStaged creates a volume with data and a live DEK ref, returning the ref.
func g02aStaged(t *testing.T, p *Plugin, k *g02aKMS, volumeID string) string {
	t.Helper()
	ctx := t.Context()
	if _, err := p.CreateVolume(ctx, volumeID, volumeID); err != nil {
		t.Fatalf("CreateVolume(%s): %v", volumeID, err)
	}
	if n, _, err := p.PutVolumeData(ctx, volumeID, volumeID, []byte("payload")); err != nil || n == 0 {
		t.Fatalf("PutVolumeData(%s): bytes=%d err=%v", volumeID, n, err)
	}
	ref := k.firstRef()
	if ref == "" {
		t.Fatal("put did not register a DEK ref")
	}
	return ref
}

func TestA199Srv_EveryEntryAnswersInvalidArgument(t *testing.T) {
	root := t.TempDir()
	k, srv := g02aMockKMS(t)
	node, ctrl, ext := g02aServices(g02aPlugin(t, root, srv))
	ctx := t.Context()
	base := t.TempDir()

	hostile := []string{"../../evil", "a/b", "vol\nFAKE plugin log line", ".", "..", "vol;rm -rf /"}
	for _, id := range hostile {
		calls := []struct {
			Name string
			Run  func() error
		}{
			{"Attach", func() error {
				_, err := node.Attach(ctx, g02aAttach(id, "sb-1", base, "", 0))
				return err
			}},
			{"Detach", func() error { _, err := node.Detach(ctx, g02aDetach(id, "sb-1", 0, nil)); return err }},
			{"Create", func() error { _, err := ctrl.Create(ctx, g02aCreate(id)); return err }},
			{"Destroy", func() error { _, err := ctrl.Destroy(ctx, g02aDestroy(id)); return err }},
			{"PutVolumeData", func() error { _, err := ext.PutVolumeData(ctx, g02aPut(id, []byte("x"))); return err }},
			{"GetVolumeInfo", func() error { _, err := ext.GetVolumeInfo(ctx, g02aInfo(id)); return err }},
		}
		for _, c := range calls {
			err := c.Run()
			if status.Code(err) != codes.InvalidArgument {
				t.Fatalf("%s(%q) returned %v (code %s), want InvalidArgument: an id that cannot be one path "+
					"segment must be refused at the RPC boundary instead of reaching paths() and surfacing a "+
					"500-class code (A199)", c.Name, id, err, status.Code(err))
			}
		}
	}

	requests, _ := k.counts()
	if requests > 0 {
		t.Fatalf("rejected volume_ids still reached the KMS (%d requests)", requests)
	}
	ents, err := os.ReadDir(root)
	if err == nil && len(ents) > 0 {
		names := make([]string, 0, len(ents))
		for _, e := range ents {
			names = append(names, e.Name())
		}
		t.Fatalf("rejected requests wrote inside the volume root: %v", names)
	}
}

// TestA199Srv_RejectionNeverEchoesRawID: the rejection text goes to the client and
// into the plugin log, so it may not carry the attacker's bytes verbatim.
func TestA199Srv_RejectionNeverEchoesRawID(t *testing.T) {
	root := t.TempDir()
	_, srv := g02aMockKMS(t)
	node, ctrl, ext := g02aServices(g02aPlugin(t, root, srv))
	ctx := t.Context()

	raw := "vol-9\nOct 19 00:00:00 host cds[1]: FORGED audit line"
	cases := []struct {
		Name string
		err  error
	}{
		{"Detach", second(node.Detach(ctx, g02aDetach(raw, "sb-1", 0, nil)))},
		{"Destroy", second(ctrl.Destroy(ctx, g02aDestroy(raw)))},
		{"GetVolumeInfo", second(ext.GetVolumeInfo(ctx, g02aInfo(raw)))},
	}
	for _, c := range cases {
		if status.Code(c.err) != codes.InvalidArgument {
			t.Fatalf("%s(%q): got %v, want InvalidArgument", c.Name, raw, c.err)
		}
		if strings.Contains(c.err.Error(), raw) {
			t.Fatalf("%s echoed the raw volume_id back to the caller: %q", c.Name, c.err)
		}
		if strings.Contains(c.err.Error(), "\n") {
			t.Fatalf("%s returned a multi-line error, so one attacker byte becomes two log lines: %q", c.Name, c.err)
		}
	}
}

// TestA199Srv_LogLineCannotBeForgedByVolumeID: the refuse paths log.Printf the id.
func TestA199Srv_LogLineCannotBeForgedByVolumeID(t *testing.T) {
	root := t.TempDir()
	_, srv := g02aMockKMS(t)
	node, _, _ := g02aServices(g02aPlugin(t, root, srv))

	var buf bytes.Buffer
	log.SetOutput(&buf)
	t.Cleanup(func() { log.SetOutput(os.Stderr) })

	_, err := node.Attach(t.Context(), g02aAttach("vol-1\nFAKE: attach succeeded for someone else", "sb-1", t.TempDir(), "", 0))
	if status.Code(err) != codes.InvalidArgument {
		t.Fatalf("Attach with a forged id: %v", err)
	}
	for _, line := range strings.Split(buf.String(), "\n") {
		if strings.HasPrefix(line, "FAKE") {
			t.Fatalf("the volume_id wrote its own log line (log forgery, A199): %q", buf.String())
		}
	}
}

// TestA199Srv_ReadSandboxFileRejectsHostileIDs: cubebox_id is interpolated into a
// filepath.Glob pattern and session_id names a host-side rootfs file, so neither
// may carry glob metacharacters, path separators or control bytes.
func TestA199Srv_ReadSandboxFileRejectsHostileIDs(t *testing.T) {
	root := t.TempDir()
	_, srv := g02aMockKMS(t)
	_, _, ext := g02aServices(g02aPlugin(t, root, srv))
	ctx := t.Context()

	for _, id := range []string{"*", "a*", "?a", "a/b", "../../etc", "id\nforged"} {
		_, err := ext.ReadSandboxFile(ctx, g02aRead(id, "sess-1", "out/result.csv"))
		if status.Code(err) != codes.InvalidArgument {
			t.Fatalf("ReadSandboxFile(cubebox_id=%q) returned %v (code %s), want InvalidArgument: a glob "+
				"metacharacter must never reach filepath.Glob (A199)", id, err, status.Code(err))
		}
		_, err = ext.ReadSandboxFile(ctx, g02aRead("0f1e2d3c4b5a69788796a5b4c3d2e1f0", id, "out/result.csv"))
		if status.Code(err) != codes.InvalidArgument {
			t.Fatalf("ReadSandboxFile(session_id=%q) returned %v (code %s), want InvalidArgument: session_id "+
				"selects the host-side rootfs file (A199)", id, err, status.Code(err))
		}
	}

	_, err := ext.ReadSandboxFile(ctx, g02aRead("0f1e2d3c4b5a69788796a5b4c3d2e1f0", "5b3e1f0a-0000-0000-0000-000000000001", "out/result.csv"))
	if status.Code(err) == codes.InvalidArgument {
		t.Fatalf("ReadSandboxFile refused legitimate ids (32-hex cubebox + uuid session): %v", err)
	}
}

// TestA199Srv_LegitimateCallsStillWork: the guard against an over-tight boundary.
func TestA199Srv_LegitimateCallsStillWork(t *testing.T) {
	root := t.TempDir()
	k, srv := g02aMockKMS(t)
	p := g02aPlugin(t, root, srv)
	node, ctrl, ext := g02aServices(p)
	ctx := t.Context()

	const id = "vol-a199-ok"
	ref := g02aStaged(t, p, k, id)

	resp, err := node.Attach(ctx, g02aAttach(id, "sb-1", t.TempDir(), g02aPrivData(ref), 0))
	if err != nil {
		t.Fatalf("legitimate Attach: %v", err)
	}
	if resp.GetHostPath() == "" {
		t.Fatal("legitimate Attach returned no host path")
	}
	if _, err := node.Detach(ctx, g02aDetach(id, "sb-1", 0, resp.GetMetadata())); err != nil {
		t.Fatalf("legitimate Detach: %v", err)
	}
	if _, err := ext.GetVolumeInfo(ctx, g02aInfo(id)); err != nil {
		t.Fatalf("legitimate GetVolumeInfo: %v", err)
	}
	if _, err := ctrl.Destroy(ctx, g02aDestroy(id)); err != nil {
		t.Fatalf("legitimate Destroy: %v", err)
	}
}

func second[T any](_ T, err error) error { return err }
