package vol

import (
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"strings"
	"syscall"
)

// A196 (OCR 2026-10-18): everything under the read-only loop mount is written by
// the GUEST, and this plugin runs as root in the HOST namespace. Resolving a
// guest path by string join and then os.ReadFile-ing it therefore turns a
// symlink in the overlay upper layer into an arbitrary host file read, and a
// FIFO or device node into a permanent block (which also strands the deferred
// umount and leaks the loop mount).
//
// The guard below is the only sanctioned way to read a guest path back:
// resolution happens in the kernel via openat2(RESOLVE_BENEATH|
// RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS|RESOLVE_NO_XDEV) with O_NOFOLLOW and
// O_NONBLOCK, the entry must be a REGULAR file per fstat on that very fd, and
// the bytes are read from the fd (never re-opened by path) under a hard bound.
var (
	// ErrSandboxPathEscape: the path leaves the mounted upper layer.
	ErrSandboxPathEscape = errors.New("escapes the sandbox upper layer")
	// ErrSandboxPathSymlink: a symlink component was refused (any component).
	ErrSandboxPathSymlink = errors.New("refuses to follow a symlink")
	// ErrSandboxPathNotRegular: fifo/socket/device/dir — never read.
	ErrSandboxPathNotRegular = errors.New("not a regular file")
	// ErrSandboxFileTooLarge: the fstat size or the read exceeds the bound.
	ErrSandboxFileTooLarge = errors.New("exceeds the copy-out bound")
)

// sandboxPathError names the offending guest path for the audit log while
// staying errors.Is()-able against the sentinels above.
type sandboxPathError struct {
	rel    string
	kind   error
	detail error
}

func (e *sandboxPathError) Error() string {
	if e.detail != nil {
		return fmt.Sprintf("sandbox path %s: %v: %v", e.rel, e.kind, e.detail)
	}
	return fmt.Sprintf("sandbox path %s: %v", e.rel, e.kind)
}

func (e *sandboxPathError) Unwrap() []error { return []error{e.kind, e.detail} }

// ReadSandboxTreeFile reads <baseDir>/<rel> where rel is fully guest-controlled
// and baseDir is a directory this plugin opened itself (production passes the
// read-only mountpoint, so the disk/<cubebox>/upper prefix is covered too).
//
// (nil, false, nil) means "not there yet" — the control plane polls for marker
// files, so absent paths and directories keep that shape. Every hostile shape is
// a typed error instead.
func ReadSandboxTreeFile(baseDir, rel string, maxBytes int64) (data []byte, exists bool, err error) {
	rel = strings.TrimLeft(rel, "/")
	base, err := os.Open(baseDir)
	if err != nil {
		return nil, false, fmt.Errorf("open sandbox tree %s: %w", baseDir, err)
	}
	defer base.Close()

	f, err := openContainedNoFollow(int(base.Fd()), rel)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, false, nil
		}
		kind := ErrSandboxPathEscape
		switch {
		case errors.Is(err, syscall.ELOOP):
			kind = ErrSandboxPathSymlink
		case errors.Is(err, syscall.ENXIO), errors.Is(err, syscall.ENODEV), errors.Is(err, syscall.EPERM):
			// The kernel itself refuses to open an irregular inode (socket, or a
			// device node whose driver is absent) — same refusal class as the
			// fstat IsRegular gate below, reported before any blocking read.
			kind = ErrSandboxPathNotRegular
		}
		return nil, false, &sandboxPathError{rel: rel, kind: kind, detail: err}
	}
	defer f.Close()

	// fstat on the fd that will be read: no second path lookup, so the guest
	// cannot swap the entry between the check and the read.
	fstat, err := f.Stat()
	if err != nil {
		return nil, false, &sandboxPathError{rel: rel, kind: ErrSandboxPathEscape, detail: err}
	}
	if fstat.IsDir() {
		return nil, false, nil
	}
	if !fstat.Mode().IsRegular() {
		return nil, false, &sandboxPathError{rel: rel, kind: ErrSandboxPathNotRegular, detail: fmt.Errorf("mode %s", fstat.Mode())}
	}
	if fstat.Size() > maxBytes {
		return nil, false, &sandboxPathError{rel: rel, kind: ErrSandboxFileTooLarge, detail: fmt.Errorf("fstat size %d > bound %d", fstat.Size(), maxBytes)}
	}
	data, err = io.ReadAll(io.LimitReader(f, maxBytes+1))
	if err != nil {
		return nil, false, fmt.Errorf("read sandbox path %s: %w", rel, err)
	}
	if int64(len(data)) > maxBytes {
		return nil, false, &sandboxPathError{rel: rel, kind: ErrSandboxFileTooLarge, detail: fmt.Errorf("grew past bound %d while reading", maxBytes)}
	}
	return data, true, nil
}

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
	RootfsUpperSubdir    = "disk"
	defaultRootfsVolRoot = "/data/cubelet/storage/xfs/objects/volumes"
	MaxSandboxFileBytes  = int64(64 * 1024 * 1024) // copy-out bound
)

func (r *Plugin) rootfsVolumeRoot() string {
	if v := os.Getenv("CDS_VOLUME_PLUGIN_ROOTFS_VOLUME_ROOT"); v != "" {
		return v
	}
	return defaultRootfsVolRoot
}

// checkSandboxSelector guards the two identifiers that select a host-side rootfs
// volume directory. Glob metacharacters are the dangerous shape: cubeboxID is
// interpolated into filepath.Glob("sb-<id>-cds-rootfs-*") whenever session_id is
// absent, so a "*" or "?" turns "this sandbox" into "whichever sandbox matched
// first" — a read of someone else's rootfs (A199 rootfs leg). Refusals name the
// byte class and never the value, because this text reaches the plugin log.
func checkSandboxSelector(what, id string) error {
	switch {
	case id == "":
		return fmt.Errorf("invalid %s: empty (fail-closed)", what)
	case len(id) > 200:
		return fmt.Errorf("invalid %s: %d bytes exceeds the 200-byte limit (fail-closed)", what, len(id))
	case strings.ContainsAny(id, "*?["):
		return fmt.Errorf("invalid %s: contains a glob metacharacter, which would widen the rootfs volume lookup to another sandbox (fail-closed)", what)
	case strings.ContainsAny(id, "/\\"):
		return fmt.Errorf("invalid %s: contains a path separator (fail-closed)", what)
	case id == "." || id == ".." || strings.Contains(id, ".."):
		return fmt.Errorf("invalid %s: traverses the volume root (fail-closed)", what)
	case strings.ContainsAny(id, " \t\n\r\v\f\x00"):
		return fmt.Errorf("invalid %s: contains whitespace or a control byte (fail-closed)", what)
	}
	return nil
}

// ReadSandboxFile loop-mounts the sandbox rootfs image read-only and
// returns the bytes of <upper>/<vm_path>. exists=false when the path is
// absent (caller polls for marker files). The mount is torn down on every
// return path; a leftover mount after a crash is harmless (read-only) and
// the next call re-uses a fresh mountpoint. The rootfs volume directory is
// located by globbing sb-<cubebox>-cds-rootfs-* (one per live cubebox);
// sessionID, when given, pins the exact directory name.
func (r *Plugin) ReadSandboxFile(cubeboxID, sessionID, vmPath string) (data []byte, exists bool, err error) {
	if err := checkSandboxSelector("sandbox id", cubeboxID); err != nil {
		return nil, false, err
	}
	if sessionID != "" {
		if err := checkSandboxSelector("session id", sessionID); err != nil {
			return nil, false, err
		}
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
		if len(matches) > 1 {
			// "Whichever matched first" is not "this sandbox": with several rootfs
			// volumes live on the node, matches[0] can be another tenant's image.
			return nil, false, fmt.Errorf("rootfs volume lookup for cubebox %s is ambiguous (%d matching dirs): pass session_id (fail-closed)", cubeboxID, len(matches))
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

	// The guest wrote this tree: resolve it contained, symlink-free and
	// non-blocking, and read from the fd the guard opens (A196).
	guestRel := RootfsUpperSubdir + "/" + cubeboxID + "/upper/" + rel
	return ReadSandboxTreeFile(mountPoint, guestRel, MaxSandboxFileBytes)
}
