package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"strings"
)

// SS-14 RT-07 (2026-10-14): read a file out of a live sandbox's writable
// rootfs. Verified against the installed CubeSandbox build (v0.7.3-rc2):
// cold-start sandboxes do NOT wire declared host_dir/empty_dir/plugin
// volumes into the guest — /results, /scratch, /data are plain directories
// on the writable-rootfs overlay. The overlay's upper layer lives in a
// host XFS object file that is an ext4 image:
//
//	<data>/objects/volumes/sb-<cubebox>-cds-rootfs-<session>/<same-name>
//	    └── disk/<cubebox>/upper/...   (container writable rootfs)
//
// The file is held live by the VMM (guest writes flow through it). The
// host may therefore only mount it READ-ONLY: a loop mount for the copy-
// out window, never a second writer.
const (
	rootfsUpperSubdir    = "disk"
	defaultRootfsVolRoot = "/data/cubelet/storage/xfs/objects/volumes"
	maxSandboxFileBytes  = int64(64 * 1024 * 1024) // copy-out bound
)

func (r *Plugin) rootfsVolumeRoot() string {
	if v := os.Getenv("CDS_VOLUME_PLUGIN_ROOTFS_VOLUME_ROOT"); v != "" {
		return v
	}
	return defaultRootfsVolRoot
}

// ReadSandboxFile loop-mounts the sandbox rootfs image read-only and
// returns the bytes of <upper>/<vm_path>. exists=false when the path is
// absent (caller polls for marker files). The mount is torn down on every
// return path; a leftover mount after a crash is harmless (read-only) and
// the next call re-uses a fresh mountpoint. The rootfs volume directory is
// located by globbing sb-<cubebox>-cds-rootfs-* (one per live cubebox);
// sessionID, when given, pins the exact directory name.
func (r *Plugin) ReadSandboxFile(cubeboxID, sessionID, vmPath string) (data []byte, exists bool, err error) {
	if cubeboxID == "" || len(cubeboxID) > 200 || strings.ContainsAny(cubeboxID, "/\\") || cubeboxID == "." || cubeboxID == ".." {
		return nil, false, fmt.Errorf("invalid sandbox id %q", cubeboxID)
	}
	if sessionID != "" && (len(sessionID) > 200 || strings.ContainsAny(sessionID, "/\\") || sessionID == "." || sessionID == "..") {
		return nil, false, fmt.Errorf("invalid session id %q", sessionID)
	}
	// vmPath is resolved inside the upper dir; reject anything that could
	// escape it (relative, cleaned, no leading slash).
	clean := path.Clean("/" + vmPath)
	if strings.Contains(vmPath, "\x00") || clean == "/" {
		return nil, false, fmt.Errorf("invalid vm_path %q", vmPath)
	}
	rel := strings.TrimPrefix(clean, "/")

	var volDir string
	if sessionID != "" {
		volDir = filepath.Join(r.rootfsVolumeRoot(), fmt.Sprintf("sb-%s-cds-rootfs-%s", cubeboxID, sessionID))
	} else {
		matches, gerr := filepath.Glob(filepath.Join(r.rootfsVolumeRoot(), fmt.Sprintf("sb-%s-cds-rootfs-*", cubeboxID)))
		if gerr != nil || len(matches) == 0 {
			return nil, false, fmt.Errorf("rootfs volume dir for cubebox %s not found", cubeboxID)
		}
		volDir = matches[0]
	}
	image := filepath.Join(volDir, filepath.Base(volDir))
	fi, err := os.Stat(image)
	if err != nil {
		return nil, false, fmt.Errorf("rootfs image %s: %w", image, err)
	}
	if fi.IsDir() {
		return nil, false, fmt.Errorf("rootfs image %s is a directory", image)
	}

	mountPoint, err := os.MkdirTemp("/run/cds-volume-plugin", "ro-")
	if err != nil {
		return nil, false, fmt.Errorf("mountpoint: %w", err)
	}
	defer os.RemoveAll(mountPoint)

	mnt := exec.Command("mount", "-o", "ro,loop", image, mountPoint)
	if out, merr := mnt.CombinedOutput(); merr != nil {
		return nil, false, fmt.Errorf("ro loop mount %s: %v: %s", image, merr, strings.TrimSpace(string(out)))
	}
	defer func() {
		if uerr := exec.Command("umount", mountPoint).Run(); uerr != nil {
			// best-effort teardown; the read-only mount is inert
			log.Printf("umount %s failed: %v", mountPoint, uerr)
		}
	}()

	src := filepath.Join(mountPoint, rootfsUpperSubdir, cubeboxID, "upper", rel)
	fi, err = os.Lstat(src)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, false, nil
		}
		return nil, false, fmt.Errorf("stat %s: %w", src, err)
	}
	if fi.IsDir() {
		return nil, false, nil
	}
	if fi.Size() > maxSandboxFileBytes {
		return nil, false, fmt.Errorf("%s exceeds copy-out bound (%d bytes)", rel, fi.Size())
	}
	data, err = os.ReadFile(src)
	if err != nil {
		return nil, false, fmt.Errorf("read %s: %w", src, err)
	}
	return data, true, nil
}
