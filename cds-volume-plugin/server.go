package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"os/user"
	"path/filepath"
	"strconv"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	cdsvolumev1 "cds-volume-plugin/gen/cdsvolumev1"
	volumepluginv1 "cds-volume-plugin/gen/volumepluginv1"
)

// PluginName is the driver name registered in the node Cubelet config
// (spec §2.3: VolumeSource.plugin_volume{driver:"cds-encrypted"}).
const PluginName = "cds-encrypted"

func mapErr(err error) error {
	if err == nil {
		return nil
	}
	switch {
	case IsVolumeNotFound(err):
		return status.Error(codes.NotFound, err.Error())
	default:
		return status.Error(codes.FailedPrecondition, err.Error())
	}
}

// ── Node side: VolumePluginService (Attach/Detach) ────────────────────────

type nodeService struct {
	volumepluginv1.UnimplementedVolumePluginServiceServer
	p *Plugin
}

func (s *nodeService) Attach(ctx context.Context, req *volumepluginv1.AttachRequest) (*volumepluginv1.AttachResponse, error) {
	if req.GetVolumeId() == "" || req.GetVolumeBaseDir() == "" {
		return nil, status.Error(codes.InvalidArgument, "volume_id and volume_base_dir required")
	}
	hostPath, metadata, err := s.p.Attach(ctx, req.GetVolumeId(), req.GetVolumeBaseDir(),
		req.GetSandboxId(), req.GetPrivateData(), req.GetRefCount())
	if err != nil {
		log.Printf("attach refused volume=%s sandbox=%s: %v", req.GetVolumeId(), req.GetSandboxId(), err)
		return nil, mapErr(err)
	}
	return &volumepluginv1.AttachResponse{HostPath: hostPath, Metadata: metadata}, nil
}

func (s *nodeService) Detach(ctx context.Context, req *volumepluginv1.DetachRequest) (*volumepluginv1.DetachResponse, error) {
	if err := s.p.Detach(ctx, req.GetVolumeId(), req.GetMetadata()["base_dir"], req.GetRefCount()); err != nil {
		log.Printf("detach failed volume=%s: %v", req.GetVolumeId(), err)
		return nil, mapErr(err)
	}
	return &volumepluginv1.DetachResponse{}, nil
}

// ── Controller side: VolumeControllerService (Create/Destroy) ─────────────

type controllerService struct {
	volumepluginv1.UnimplementedVolumeControllerServiceServer
	p *Plugin
}

func (s *controllerService) Create(ctx context.Context, req *volumepluginv1.CreateRequest) (*volumepluginv1.CreateResponse, error) {
	if req.GetVolumeId() == "" {
		return nil, status.Error(codes.InvalidArgument, "volume_id required")
	}
	pd, err := s.p.CreateVolume(ctx, req.GetVolumeId(), req.GetName())
	if err != nil {
		return nil, mapErr(err)
	}
	return &volumepluginv1.CreateResponse{PrivateData: pd}, nil
}

func (s *controllerService) Destroy(ctx context.Context, req *volumepluginv1.DestroyRequest) (*volumepluginv1.DestroyResponse, error) {
	if err := s.p.Destroy(ctx, req.GetVolumeId()); err != nil {
		log.Printf("destroy refused volume=%s: %v", req.GetVolumeId(), err)
		return nil, mapErr(err)
	}
	return &volumepluginv1.DestroyResponse{}, nil
}

// ── CDS extension: data path ───────────────────────────────────────────────

type extensionService struct {
	cdsvolumev1.UnimplementedCDSVolumeExtensionServer
	p *Plugin
}

func (s *extensionService) PutVolumeData(ctx context.Context, req *cdsvolumev1.PutVolumeDataRequest) (*cdsvolumev1.PutVolumeDataResponse, error) {
	n, idempotent, err := s.p.PutVolumeData(ctx, req.GetVolumeId(), req.GetAad(), req.GetData())
	if err != nil {
		return nil, mapErr(err)
	}
	return &cdsvolumev1.PutVolumeDataResponse{DataSizeBytes: n, IdempotentRetry: idempotent}, nil
}

func (s *extensionService) GetVolumeInfo(ctx context.Context, req *cdsvolumev1.GetVolumeInfoRequest) (*cdsvolumev1.GetVolumeInfoResponse, error) {
	m, err := s.p.GetVolumeInfo(req.GetVolumeId())
	if err != nil {
		return nil, mapErr(err)
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
func (s *extensionService) ReadSandboxFile(ctx context.Context, req *cdsvolumev1.ReadSandboxFileRequest) (*cdsvolumev1.ReadSandboxFileResponse, error) {
	if req.GetCubeboxId() == "" || req.GetVmPath() == "" {
		return nil, status.Error(codes.InvalidArgument, "cubebox_id and vm_path required")
	}
	data, exists, err := s.p.ReadSandboxFile(req.GetCubeboxId(), req.GetSessionId(), req.GetVmPath())
	if err != nil {
		log.Printf("read_sandbox_file failed cubebox=%s path=%s: %v", req.GetCubeboxId(), req.GetVmPath(), err)
		return nil, status.Error(codes.Internal, err.Error())
	}
	return &cdsvolumev1.ReadSandboxFileResponse{Data: data, Exists: exists, SizeBytes: uint64(len(data))}, nil
}

// Serve starts the gRPC server on the unix socket (spec: socket
// root:cds 0600 — chmod 0600; chown to the cds group is best-effort and
// logged, since the group may not exist in dev environments).
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
	if err := os.Chmod(cfg.SocketPath, 0600); err != nil {
		log.Printf("WARN: chmod 0600 on %s failed: %v", cfg.SocketPath, err)
	}
	// spec §2.3: socket root:cds 0600 — group ownership is best-effort (the
	// cds group may not exist in dev environments; Cubelet's runtime user
	// must be in that group to dial the socket).
	if g, gerr := user.LookupGroup("cds"); gerr == nil {
		gid, _ := strconv.Atoi(g.Gid)
		_ = os.Chown(cfg.SocketPath, 0, gid)
	} else {
		log.Printf("WARN: group 'cds' not present, socket %s left root-only (prod images create it): %v", cfg.SocketPath, gerr)
	}
	if err := os.Chown(cfg.SocketPath, 0, -1); err != nil {
		log.Printf("WARN: chown root on %s failed (need root; expected in dev): %v", cfg.SocketPath, err)
	}

	srv := grpc.NewServer()
	volumepluginv1.RegisterVolumePluginServiceServer(srv, &nodeService{p: p})
	volumepluginv1.RegisterVolumeControllerServiceServer(srv, &controllerService{p: p})
	cdsvolumev1.RegisterCDSVolumeExtensionServer(srv, &extensionService{p: p})

	log.Printf("cds-volume-plugin serving %s (driver=%s, backend=%s, node=%s)", cfg.SocketPath, PluginName, p.cipher.Name(), cfg.NodeID)
	return srv.Serve(lis)
}
