package vol

import (
	"context"
	"errors"
	"fmt"
	"hash/fnv"
	"log"
	"net"
	"os"
	"os/user"
	"path/filepath"
	"runtime/debug"
	"strconv"
	"strings"
	"sync"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	cdsvolumev1 "cds-volume-plugin/gen/cdsvolumev1"
	volumepluginv1 "cds-volume-plugin/gen/volumepluginv1"
)

// PluginName is the driver name registered in the node Cubelet config
// (spec §2.3: VolumeSource.plugin_volume{driver:"cds-encrypted"}).
const PluginName = "cds-encrypted"

// The layers under this file reject hostile input and impossible state with plain
// errors (plugin.go ValidateVolumeID / volumeChecked, rootfs.go id guards, layout
// write guards). Cubelet cannot act on a bare 500-class code, and every one of
// those rejections used to arrive as FailedPrecondition — indistinguishable from
// "the volume is in the wrong state, retry later", which is how an operator ends
// up retrying a request that can never succeed. MapErr re-classifies them by what
// the layer actually said. The message is carried verbatim: those layers name the
// offending byte offset or class, never the attacker's bytes (A199).

// InputRejectionPrefixes are the rejection stems layers WITHOUT typed errors emit
// today: rootfs.go id guards, layout.go:79. plugin.go rejections are classified by
// sentinel identity in IsInputRejection; this table remains only as the
// compatibility path for the text-worded layers until they carry sentinels too,
// so an entry must stay registered until its producer does. Kept as data: a new
// text-worded rejection site has to be registered here to reach the client.
var InputRejectionPrefixes = []string{
	"invalid volume_id",
	"invalid sandbox id",
	"invalid session id",
	"invalid vm_path",
}

func IsInputRejection(err error) bool {
	// Identity first: plugin.go wraps ErrInvalidVolumeID / ErrVolumeNotContained
	// with %w, so a contextual rewrap by any intermediate layer must not be able
	// to reclass an impossible-request as retryable-looking state (A199).
	if errors.Is(err, ErrInvalidVolumeID) || errors.Is(err, ErrVolumeNotContained) {
		return true
	}
	msg := err.Error()
	for _, p := range InputRejectionPrefixes {
		if strings.HasPrefix(msg, p) {
			return true
		}
	}
	// plugin.go volumeChecked: the id is well-formed but does not resolve inside
	// VolumeRoot — still a bad request, not bad state. Worded per layer (plugin.go
	// and layout.go), so match the containment phrase.
	return strings.Contains(msg, "does not resolve to")
}

func isMissing(err error) bool {
	return IsVolumeNotFound(err) || errors.Is(err, os.ErrNotExist) ||
		strings.HasSuffix(err.Error(), "not found")
}

func MapErr(err error) error {
	if err == nil {
		return nil
	}
	switch {
	case isMissing(err):
		return status.Error(codes.NotFound, err.Error())
	case IsInputRejection(err):
		return status.Error(codes.InvalidArgument, err.Error())
	default:
		return status.Error(codes.FailedPrecondition, err.Error())
	}
}

// ── RPC input boundary (A199) ───────────────────────────────────────────────

// safeIDField is the shape every identifier that names a host path must have at the
// transport boundary: one path segment of [A-Za-z0-9._-]. volume_id selects a
// VolumeRoot child, session_id selects a host-side rootfs volume dir, and cubebox_id
// is interpolated into a filepath.Glob pattern — so a separator escapes the tree and
// a `*` silently turns "this container" into "whatever matched first".
func requireSafeID(field, id string) error {
	if id == "" {
		return status.Errorf(codes.InvalidArgument, "%s is required (fail-closed)", field)
	}
	if err := ValidateVolumeID(id); err != nil {
		return status.Errorf(codes.InvalidArgument, "%s %q refused: %s (fail-closed)", field, safeIDForLog(id), safeIDReason(id))
	}
	return nil
}

// safeIDReason explains a refusal without reproducing the input: ValidateVolumeID's
// own traversal branch quotes the raw id, and this text reaches both the client and
// the plugin log (a "\n" in an id would otherwise forge a log line).
func safeIDReason(id string) string {
	switch {
	case len(id) > 200:
		return fmt.Sprintf("%d bytes exceeds the 200-byte limit", len(id))
	case strings.ContainsAny(id, "*?["):
		return "contains a glob metacharacter"
	case strings.Contains(id, "..") || id == "." || id == "..":
		return "traverses the volume root"
	case strings.ContainsAny(id, "/\\"):
		return "contains a path separator"
	default:
		return "contains a byte outside [A-Za-z0-9._-]"
	}
}

// safeIDForLog projects an attacker-controlled id into something safe to print:
// at most 40 bytes, every byte outside the whitelist replaced. It is a projection
// for operators, never a value to act on.
func safeIDForLog(id string) string {
	if len(id) > 40 {
		id = id[:40]
	}
	var b strings.Builder
	b.Grow(len(id))
	for i := 0; i < len(id); i++ {
		c := id[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
			b.WriteByte(c)
		case c == '-', c == '_', c == '.':
			b.WriteByte(c)
		default:
			b.WriteByte('_')
		}
	}
	return b.String()
}

// ── Node-side attachment ledger (A200) ──────────────────────────────────────

// refLedger remembers what this node's gRPC layer actually served, because
// DestroyRequest carries no ref_count (upstream volumepluginv1 has only volume_id)
// and plugin.Destroy only logs a WARNING for the attached case. Without it a
// controller call can crypto-erase a volume whose staged plaintext a live sandbox is
// still reading. Counts follow the upstream protocol verbatim: AttachRequest.ref_count
// is the count BEFORE that call, DetachRequest.ref_count the count AFTER it.
type refLedger struct {
	mu   sync.Mutex
	live map[string]int64
}

func newRefLedger() *refLedger {
	return &refLedger{live: make(map[string]int64)}
}

// attachments is process-wide because the plugin serves one node socket: it is the
// node's own belief about volumes it attached, not shared control-plane state.
var attachments = newRefLedger()

func (l *refLedger) noteAttach(volumeID string, preAttach int64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.live[volumeID] = preAttach + 1
}

func (l *refLedger) noteDetach(volumeID string, postDetach int64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if postDetach <= 0 {
		delete(l.live, volumeID)
		return
	}
	l.live[volumeID] = postDetach
}

func (l *refLedger) Count(volumeID string) int64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.live[volumeID]
}

// forget drops a volume after it has been destroyed, so a recycled volume_id is not
// blocked by the accounting of a volume that no longer exists.
func (l *refLedger) Forget(volumeID string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.live, volumeID)
}

// ── Transport limits and fault containment (A197) ──────────────────────────

const (
	// grpcMessageOverhead is the room left beside the payload for the rest of the
	// message (field tags and length prefixes, aad, metadata).
	grpcMessageOverhead = 1 << 20
	// grpcMessageCeiling is grpc-go's own hard limit (math.MaxInt32).
	grpcMessageCeiling = 1<<31 - 1
	// maxConcurrentStreams bounds in-flight requests per transport, so the raised
	// message cap cannot be turned into an unbounded allocator by N simultaneous
	// near-cap puts.
	maxConcurrentStreams = 32
	// VolumeLockShards bounds the per-volume serialisation table (sharded by id,
	// so no unbounded map grows with volume churn).
	VolumeLockShards = 64
)

// grpcMessageSize derives the wire cap from the volume cap. The two used to be
// unrelated: config.go promises 1 GiB volumes while grpc-go's silent 4 MiB
// receive default refused anything above it, surfacing as an unrelated transport
// error (A197).
func grpcMessageSize(cfg Config) int {
	want := cfg.MaxVolumeBytes + grpcMessageOverhead
	if want > grpcMessageCeiling {
		return grpcMessageCeiling
	}
	if want <= 0 {
		return grpcMessageOverhead
	}
	return int(want)
}

// recoverUnary keeps a panic inside one handler from tearing down the transport
// (and, on the default handler, the plugin process — which would strand every
// volume this node has mounted). Panic text and stack go to the plugin log; the
// client gets Internal without the panic text, which can carry paths.
func recoverUnary(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (resp any, err error) {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("PANIC while serving %s: %v\n%s", info.FullMethod, r, debug.Stack())
			err = status.Errorf(codes.Internal, "internal error while serving %s (recovered; see the plugin log)", info.FullMethod)
		}
	}()
	return handler(ctx, req)
}

type volumeLockTable struct {
	Shards [VolumeLockShards]sync.Mutex
}

// lock serialises the RPCs that touch one volume_id. Layout writes are guarded
// per-directory, but the RPC handlers also read-modify-write the attachment
// ledger and the manifest, so two concurrent calls for the same volume could
// interleave across those steps.
func (t *volumeLockTable) Lock(volumeID string) func() {
	h := fnv.New32a()
	_, _ = h.Write([]byte(volumeID))
	m := &t.Shards[h.Sum32()%VolumeLockShards]
	m.Lock()
	return m.Unlock
}

var VolumeLocks volumeLockTable

// ── Node side: VolumePluginService (Attach/Detach) ────────────────────────

type NodeService struct {
	volumepluginv1.UnimplementedVolumePluginServiceServer
	p *Plugin
}

func (s *NodeService) Attach(ctx context.Context, req *volumepluginv1.AttachRequest) (*volumepluginv1.AttachResponse, error) {
	if req.GetVolumeBaseDir() == "" {
		return nil, status.Error(codes.InvalidArgument, "volume_base_dir is required (fail-closed)")
	}
	if err := requireSafeID("volume_id", req.GetVolumeId()); err != nil {
		return nil, err
	}
	if req.GetSandboxId() == "" {
		return nil, status.Error(codes.InvalidArgument, "sandbox_id is required (fail-closed)")
	}
	defer VolumeLocks.Lock(req.GetVolumeId())()
	hostPath, metadata, err := s.p.Attach(ctx, req.GetVolumeId(), req.GetVolumeBaseDir(),
		req.GetSandboxId(), req.GetPrivateData(), req.GetRefCount())
	if err != nil {
		log.Printf("attach refused volume=%s sandbox=%s: %v", safeIDForLog(req.GetVolumeId()), safeIDForLog(req.GetSandboxId()), err)
		return nil, MapErr(err)
	}
	attachments.noteAttach(req.GetVolumeId(), req.GetRefCount())
	return &volumepluginv1.AttachResponse{HostPath: hostPath, Metadata: metadata}, nil
}

func (s *NodeService) Detach(ctx context.Context, req *volumepluginv1.DetachRequest) (*volumepluginv1.DetachResponse, error) {
	if err := requireSafeID("volume_id", req.GetVolumeId()); err != nil {
		return nil, err
	}
	defer VolumeLocks.Lock(req.GetVolumeId())()
	if err := s.p.Detach(ctx, req.GetVolumeId(), req.GetMetadata()["base_dir"], req.GetRefCount()); err != nil {
		log.Printf("detach failed volume=%s: %v", safeIDForLog(req.GetVolumeId()), err)
		return nil, MapErr(err)
	}
	attachments.noteDetach(req.GetVolumeId(), req.GetRefCount())
	return &volumepluginv1.DetachResponse{}, nil
}

// ── Controller side: VolumeControllerService (Create/Destroy) ─────────────

type ControllerService struct {
	volumepluginv1.UnimplementedVolumeControllerServiceServer
	p *Plugin
}

// NewNodeService / NewControllerService / NewExtensionService build the gRPC
// service wrappers around a Plugin. Exported so external tests (and any future
// composition root) can construct them without touching unexported fields.
func NewNodeService(p *Plugin) *NodeService { return &NodeService{p: p} }

func NewControllerService(p *Plugin) *ControllerService { return &ControllerService{p: p} }

func NewExtensionService(p *Plugin) *ExtensionService { return &ExtensionService{p: p} }

func (s *ControllerService) Create(ctx context.Context, req *volumepluginv1.CreateRequest) (*volumepluginv1.CreateResponse, error) {
	if err := requireSafeID("volume_id", req.GetVolumeId()); err != nil {
		return nil, err
	}
	defer VolumeLocks.Lock(req.GetVolumeId())()
	pd, err := s.p.CreateVolume(ctx, req.GetVolumeId(), req.GetName())
	if err != nil {
		return nil, MapErr(err)
	}
	return &volumepluginv1.CreateResponse{PrivateData: pd}, nil
}

func (s *ControllerService) Destroy(ctx context.Context, req *volumepluginv1.DestroyRequest) (*volumepluginv1.DestroyResponse, error) {
	if err := requireSafeID("volume_id", req.GetVolumeId()); err != nil {
		return nil, err
	}
	defer VolumeLocks.Lock(req.GetVolumeId())()
	if n := attachments.Count(req.GetVolumeId()); n > 0 {
		return nil, status.Errorf(codes.FailedPrecondition,
			"volume %q still has %d attachment(s) served by this node: Detach them before Destroy (fail-closed, "+
				"crypto-erase would revoke the DEK and shred the tree under a live sandbox)", safeIDForLog(req.GetVolumeId()), n)
	}
	if err := s.p.Destroy(ctx, req.GetVolumeId()); err != nil {
		log.Printf("destroy refused volume=%s: %v", safeIDForLog(req.GetVolumeId()), err)
		return nil, MapErr(err)
	}
	attachments.Forget(req.GetVolumeId())
	return &volumepluginv1.DestroyResponse{}, nil
}

// ── CDS extension: data path ───────────────────────────────────────────────

type ExtensionService struct {
	cdsvolumev1.UnimplementedCDSVolumeExtensionServer
	p *Plugin
}

func (s *ExtensionService) PutVolumeData(ctx context.Context, req *cdsvolumev1.PutVolumeDataRequest) (*cdsvolumev1.PutVolumeDataResponse, error) {
	if err := requireSafeID("volume_id", req.GetVolumeId()); err != nil {
		return nil, err
	}
	defer VolumeLocks.Lock(req.GetVolumeId())()
	n, idempotent, err := s.p.PutVolumeData(ctx, req.GetVolumeId(), req.GetAad(), req.GetData())
	if err != nil {
		return nil, MapErr(err)
	}
	return &cdsvolumev1.PutVolumeDataResponse{DataSizeBytes: n, IdempotentRetry: idempotent}, nil
}

func (s *ExtensionService) GetVolumeInfo(ctx context.Context, req *cdsvolumev1.GetVolumeInfoRequest) (*cdsvolumev1.GetVolumeInfoResponse, error) {
	if err := requireSafeID("volume_id", req.GetVolumeId()); err != nil {
		return nil, err
	}
	defer VolumeLocks.Lock(req.GetVolumeId())()
	m, err := s.p.GetVolumeInfo(req.GetVolumeId())
	if err != nil {
		return nil, MapErr(err)
	}
	return &cdsvolumev1.GetVolumeInfoResponse{
		VolumeId: m.VolumeID, Name: m.Name, Cipher: m.Cipher,
		DekRef: m.DekRef, DataSizeBytes: m.DataSizeBytes, HasData: m.HasData,
	}, nil
}

// ReadSandboxFile reads a file out of a live sandbox's writable rootfs
// (SS-14 RT-07, 2026-10-14). Upstream cold-start sandboxes do not wire
// declared volumes into the guest (verified 2026-10-14: /results, /scratch,
// /data are plain dirs on the writable-rootfs overlay), so the control
// plane recovers exec output / sandbox files from the host-side rootfs
// image: a READ-ONLY loop mount of the ext4 file, copy out, unmount.
// The host never writes into the file (double-mount corrupt risk).
func (s *ExtensionService) ReadSandboxFile(ctx context.Context, req *cdsvolumev1.ReadSandboxFileRequest) (*cdsvolumev1.ReadSandboxFileResponse, error) {
	if req.GetVmPath() == "" {
		return nil, status.Error(codes.InvalidArgument, "vm_path is required (fail-closed)")
	}
	if err := requireSafeID("cubebox_id", req.GetCubeboxId()); err != nil {
		return nil, err
	}
	if err := requireSafeID("session_id", req.GetSessionId()); err != nil {
		return nil, err
	}
	data, exists, err := s.p.ReadSandboxFile(req.GetCubeboxId(), req.GetSessionId(), req.GetVmPath())
	if err != nil {
		log.Printf("read_sandbox_file failed cubebox=%s session=%s: %v",
			safeIDForLog(req.GetCubeboxId()), safeIDForLog(req.GetSessionId()), err)
		return nil, MapErr(err)
	}
	return &cdsvolumev1.ReadSandboxFileResponse{Data: data, Exists: exists, SizeBytes: uint64(len(data))}, nil
}

// socketMode is the declared permission of the plugin socket (spec §2.3).
const socketMode os.FileMode = 0600

// EnforceSocketAccess turns the declared socket posture (root:cds 0600) into a
// verified fact or a refusal. Each of these operations used to be a best-effort
// `log WARN`: an image that runs the plugin as a non-root user served a socket it
// could not own, and could never chown a staged tree or loop-mount a rootfs,
// while the log still looked healthy (A205①).
func EnforceSocketAccess(cfg Config) error {
	if err := os.Chmod(cfg.SocketPath, socketMode); err != nil {
		return fmt.Errorf("cannot enforce mode 0600 on %s: %w (fail-closed: a wider socket hands the DEK-bearing plugin to any local user)", cfg.SocketPath, err)
	}
	st, err := os.Stat(cfg.SocketPath)
	if err != nil {
		return fmt.Errorf("stat socket %s: %w", cfg.SocketPath, err)
	}
	if st.Mode().Perm() != socketMode {
		return fmt.Errorf("socket %s ended up %04o, declared 0600 (fail-closed)", cfg.SocketPath, st.Mode().Perm())
	}

	gid := -1
	groupDeclared := false
	if g, gerr := user.LookupGroup("cds"); gerr == nil {
		n, cerr := strconv.Atoi(g.Gid)
		if cerr != nil {
			return fmt.Errorf("group 'cds' has non-numeric gid %q: %w", g.Gid, cerr)
		}
		gid, groupDeclared = n, true
	} else if os.Geteuid() == 0 {
		return fmt.Errorf("declared group 'cds' is absent (%w): refusing to serve, because 0600 with no group owner leaves the Cubelet runtime user unable to dial the socket at all (A205①)", gerr)
	}

	if err := os.Chown(cfg.SocketPath, 0, gid); err != nil {
		if os.Geteuid() == 0 {
			return fmt.Errorf("cannot take ownership of %s as root: %w (fail-closed)", cfg.SocketPath, err)
		}
		if !cfg.AllowNonRoot {
			return fmt.Errorf("plugin runs as uid %d, not root: it cannot take ownership of %s, cannot chown staged plaintext to the runtime user and cannot loop-mount a sandbox rootfs, so Attach/ReadSandboxFile would fail at runtime. Run as root, or set %s=1 to serve in a disclosed degraded mode",
				os.Geteuid(), cfg.SocketPath, EnvAllowNonRoot)
		}
		log.Printf("WARNING: socket %s stays owned by uid %d instead of root:cds (mode 0600 IS verified, so no other local user can dial it), and staged-tree chown plus every read-only loop mount below will fail at runtime — degraded dev mode via %s=1, NOT a compliant node (A205①)",
			cfg.SocketPath, os.Geteuid(), EnvAllowNonRoot)
	}
	if !groupDeclared {
		log.Printf("WARNING: socket %s has no 'cds' group owner; 0600 means only uid %d may dial it (prod images create the group)", cfg.SocketPath, os.Geteuid())
	}
	return nil
}

// NewGRPCServer builds the server with the limits the plugin's own contract
// implies and registers the three services. Serve and the tests share this one
// construction path, so a limit can never be proven on a server production does
// not build.
func NewGRPCServer(cfg Config, p *Plugin) *grpc.Server {
	size := grpcMessageSize(cfg)
	srv := grpc.NewServer(
		grpc.MaxRecvMsgSize(size),
		grpc.MaxSendMsgSize(size),
		grpc.MaxConcurrentStreams(maxConcurrentStreams),
		grpc.ChainUnaryInterceptor(recoverUnary),
	)
	volumepluginv1.RegisterVolumePluginServiceServer(srv, NewNodeService(p))
	volumepluginv1.RegisterVolumeControllerServiceServer(srv, NewControllerService(p))
	cdsvolumev1.RegisterCDSVolumeExtensionServer(srv, NewExtensionService(p))
	return srv
}

// Serve starts the gRPC server on the unix socket (spec: socket
// root:cds 0600, ownership verified by EnforceSocketAccess).
func Serve(cfg Config, p *Plugin) error {
	socketDir := filepath.Dir(cfg.SocketPath)
	if err := os.MkdirAll(socketDir, 0711); err != nil {
		return fmt.Errorf("socket dir: %w", err)
	}
	_ = os.Remove(cfg.SocketPath)
	lis, err := net.Listen("unix", cfg.SocketPath)
	if err != nil {
		return fmt.Errorf("listen %s: %w", cfg.SocketPath, err)
	}
	if err := EnforceSocketAccess(cfg); err != nil {
		return err
	}

	srv := NewGRPCServer(cfg, p)

	log.Printf("cds-volume-plugin serving %s (driver=%s, backend=%s, node=%s)", cfg.SocketPath, PluginName, p.cipher.Name(), cfg.NodeID)
	return srv.Serve(lis)
}
