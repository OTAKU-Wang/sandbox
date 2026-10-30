//go:build linux

package main

import (
	"fmt"
	"os"

	"golang.org/x/sys/unix"
)

// openContainedNoFollow opens rel beneath dirFd as a single kernel operation
// that cannot be talked out of its containment (A196):
//
//	RESOLVE_BENEATH      — no .. / absolute path may leave dirFd
//	RESOLVE_NO_SYMLINKS  — no symlink component, at any depth
//	RESOLVE_NO_MAGICLINKS— no /proc-style magic links either
//	RESOLVE_NO_XDEV      — no crossing into another mount
//	O_NOFOLLOW           — belt and braces for the final component
//	O_NONBLOCK           — a guest-created FIFO/device node is refused by the
//	                       later fstat check instead of blocking the open, which
//	                       is what used to strand the umount and leak the mount
//
// The returned *os.File is the only handle to the bytes: callers fstat it and
// read from it, never re-resolving the path.
func openContainedNoFollow(dirFd int, rel string) (*os.File, error) {
	if rel == "" {
		return nil, fmt.Errorf("empty sandbox path")
	}
	how := &unix.OpenHow{
		Flags: uint64(unix.O_RDONLY | unix.O_CLOEXEC | unix.O_NOFOLLOW | unix.O_NONBLOCK),
		Resolve: unix.RESOLVE_BENEATH | unix.RESOLVE_NO_SYMLINKS |
			unix.RESOLVE_NO_MAGICLINKS | unix.RESOLVE_NO_XDEV,
	}
	fd, err := unix.Openat2(dirFd, rel, how)
	if err != nil {
		return nil, fmt.Errorf("openat2 %q (contained, no-symlinks): %w", rel, err)
	}
	return os.NewFile(uintptr(fd), rel), nil
}
