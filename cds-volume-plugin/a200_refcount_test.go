package main

// A200 (refcount accounting half, OCR 2026-10-18, high) — DestroyRequest carries
// only volume_id (gen/volumepluginv1: no ref_count field) and the server kept no
// bookkeeping, so CubeMaster — or any caller of the controller socket — could
// crypto-erase a volume that this node had just attached: the DEK is revoked, the
// ciphertext tree shredded, while a live sandbox still reads its staged
// plaintext. plugin.go only logs a WARNING for that state.
//
// The upstream proto cannot gain a ref_count without protoc + an upstream
// volumeplugin.proto change (NEEDS-TOOLCHAIN, see evidence), but the node side
// already receives the counts it needs: AttachRequest.ref_count is the PRE-attach
// count and DetachRequest.ref_count is the POST-detach count (verbatim proto
// comments), so the gRPC layer can track what it served and refuse Destroy.

import (
	"os"
	"path/filepath"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func g02aManifestPath(root, volumeID string) string {
	return filepath.Join(root, volumeID, "manifest.json")
}

func TestA200Refs_DestroyRefusedWhileVolumeIsAttached(t *testing.T) {
	root := t.TempDir()
	k, srv := g02aMockKMS(t)
	p := g02aPlugin(t, root, srv)
	node, ctrl, _ := g02aServices(p)
	ctx := t.Context()

	const id = "vol-destroy-while-attached"
	ref := g02aStaged(t, p, k, id)

	resp, err := node.Attach(ctx, g02aAttach(id, "sb-1", t.TempDir(), g02aPrivData(ref), 0))
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	if resp.GetHostPath() == "" {
		t.Fatal("Attach returned no host path")
	}

	_, derr := ctrl.Destroy(ctx, g02aDestroy(id))
	if status.Code(derr) != codes.FailedPrecondition {
		t.Fatalf("Destroy of a volume this node just attached returned %v (code %s), want FailedPrecondition: "+
			"crypto-erasing an attached volume revokes the DEK and shreds the tree under a live sandbox (A200)",
			derr, status.Code(derr))
	}

	if _, revoked := k.counts(); revoked > 0 {
		t.Fatalf("the refused Destroy still revoked the DEK: crypto-erase must not start once the request is refused")
	}
	if _, statErr := os.Stat(g02aManifestPath(root, id)); statErr != nil {
		t.Fatalf("the refused Destroy still removed the volume tree: %v", statErr)
	}
	staged := filepath.Join(resp.GetHostPath(), "data")
	if _, statErr := os.Stat(staged); statErr != nil {
		t.Fatalf("the live sandbox's staged plaintext disappeared behind a refused Destroy: %v", statErr)
	}
}

func TestA200Refs_DestroyAllowedAfterLastDetach(t *testing.T) {
	root := t.TempDir()
	k, srv := g02aMockKMS(t)
	p := g02aPlugin(t, root, srv)
	node, ctrl, _ := g02aServices(p)
	ctx := t.Context()

	const id = "vol-destroy-after-detach"
	ref := g02aStaged(t, p, k, id)

	resp, err := node.Attach(ctx, g02aAttach(id, "sb-1", t.TempDir(), g02aPrivData(ref), 0))
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	if _, err := node.Detach(ctx, g02aDetach(id, "sb-1", 0, resp.GetMetadata())); err != nil {
		t.Fatalf("Detach (post count 0): %v", err)
	}
	if _, err := ctrl.Destroy(ctx, g02aDestroy(id)); err != nil {
		t.Fatalf("Destroy after the last detach must still work, got %v (A200 over-tightening guard)", err)
	}
	if _, revoked := k.counts(); revoked != 1 {
		t.Fatalf("Destroy must still crypto-erase once the volume is detached")
	}
}

// TestA200Refs_SharedVolumeStaysProtectedUntilLastDetach drives the upstream
// counting contract: Attach.ref_count is the count BEFORE the call, Detach.ref_count
// the count AFTER it, so a volume shared by two sandboxes must stay protected while
// one of them still holds it.
func TestA200Refs_SharedVolumeStaysProtectedUntilLastDetach(t *testing.T) {
	root := t.TempDir()
	k, srv := g02aMockKMS(t)
	p := g02aPlugin(t, root, srv)
	node, ctrl, _ := g02aServices(p)
	ctx := t.Context()

	const id = "vol-shared"
	ref := g02aStaged(t, p, k, id)

	first, err := node.Attach(ctx, g02aAttach(id, "sb-1", t.TempDir(), g02aPrivData(ref), 0))
	if err != nil {
		t.Fatalf("Attach sb-1: %v", err)
	}
	if _, err := node.Attach(ctx, g02aAttach(id, "sb-2", t.TempDir(), g02aPrivData(ref), 1)); err != nil {
		t.Fatalf("Attach sb-2 (pre-attach count 1): %v", err)
	}

	if _, err := node.Detach(ctx, g02aDetach(id, "sb-1", 1, first.GetMetadata())); err != nil {
		t.Fatalf("Detach sb-1 (post-detach count 1): %v", err)
	}
	if _, derr := ctrl.Destroy(ctx, g02aDestroy(id)); status.Code(derr) != codes.FailedPrecondition {
		t.Fatalf("Destroy while a second sandbox still holds the volume returned %v (code %s), want "+
			"FailedPrecondition (A200)", derr, status.Code(derr))
	}

	if _, err := node.Detach(ctx, g02aDetach(id, "sb-2", 0, first.GetMetadata())); err != nil {
		t.Fatalf("Detach sb-2 (post-detach count 0): %v", err)
	}
	if _, err := ctrl.Destroy(ctx, g02aDestroy(id)); err != nil {
		t.Fatalf("Destroy after the last detach: %v", err)
	}
}

// TestA200Refs_FailedAttachLeavesNoReference: a refused attach must not create a
// phantom attachment that blocks Destroy for ever.
func TestA200Refs_FailedAttachLeavesNoReference(t *testing.T) {
	root := t.TempDir()
	k, srv := g02aMockKMS(t)
	p := g02aPlugin(t, root, srv)
	node, ctrl, _ := g02aServices(p)
	ctx := t.Context()

	const id = "vol-attach-failed"
	g02aStaged(t, p, k, id)

	if _, err := node.Attach(ctx, g02aAttach(id, "sb-1", t.TempDir(), "not-json", 0)); err == nil {
		t.Fatal("Attach with an unreadable private_data annotation must fail")
	}
	if _, err := ctrl.Destroy(ctx, g02aDestroy(id)); err != nil {
		t.Fatalf("Destroy after a FAILED attach returned %v: a refused attach must not leave a reference "+
			"behind that blocks the volume for ever (A200)", err)
	}
}

// TestA200Refs_DestroyOfUnknownVolumeStillReportsNotFound: the ledger must not
// change the answer for a volume this node never attached.
func TestA200Refs_DestroyOfUnknownVolumeStillReportsNotFound(t *testing.T) {
	root := t.TempDir()
	_, srv := g02aMockKMS(t)
	_, ctrl, _ := g02aServices(g02aPlugin(t, root, srv))

	_, err := ctrl.Destroy(t.Context(), g02aDestroy("vol-never-created"))
	if status.Code(err) != codes.NotFound {
		t.Fatalf("Destroy of an unknown volume returned %v (code %s), want NotFound", err, status.Code(err))
	}
}
