//go:build !linux

package main

import (
	"errors"
	"os"
)

// ErrSandboxReadUnsupported is returned off Linux: the containment guarantee the
// read-back path relies on is Linux openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)
// (A196), and re-implementing it with lstat-plus-open would reintroduce exactly
// the follow-a-symlink race the guard exists to close. Fail closed instead
// (铁律: 禁降级).
var ErrSandboxReadUnsupported = errors.New("sandbox read-back needs Linux openat2 containment (fail-closed)")

func openContainedNoFollow(dirFd int, rel string) (*os.File, error) {
	return nil, ErrSandboxReadUnsupported
}
