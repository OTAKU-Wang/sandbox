package vol_test

import . "cds-volume-plugin/internal/vol"

// A196 read-back safety contract (docs/ocr-review-2026-10-18/ocr-verified-findings.md
// §A196, critical/活路径): the copy-out path resolves a guest-controlled path
// inside a read-only loop mount, as ROOT, in the HOST namespace. Today it
// `Lstat`s and then `os.ReadFile`s that path, so a symlink the sandbox wrote
// into the overlay upper layer turns into an arbitrary host file read, and a
// FIFO or device node blocks the copy out forever (which also strands the
// deferred `umount` and leaks the loop mount).
//
// Pinned contract for the guard helper the production path must use:
//   - resolution is contained by the kernel: openat2(RESOLVE_BENEATH|
//     RESOLVE_NO_SYMLINKS) — no symlink component, no `..` escape, no crossing
//     to another filesystem;
//   - the entry must be a REGULAR file, re-checked by fstat on the fd that is
//     then read (never re-opened by path);
//   - the size bound comes from that fstat, and the read is capped;
//   - a FIFO/socket/device/symlink is a typed REFUSAL, and refusing must not
//     block: O_NONBLOCK means no writer can hold the plugin hostage.
//
// Absent path stays (nil, false, nil) and a directory stays (nil, false, nil)
// — the control plane polls for marker files and must keep that semantics.

import (
	"errors"
	"io/fs"
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// sandboxTree lays out a stand-in for a mounted overlay upper dir:
//
//	<root>/upper/results/ok.bin        (legit guest output)
//	<root>/upper/results/<hostiles…>
//	<root>/secret                      (foreign file, OUTSIDE upper)
type sandboxTree struct {
	root       string
	upper      string
	secretPath string
	secretBody []byte
}

func newSandboxTree(t *testing.T) *sandboxTree {
	t.Helper()
	root := t.TempDir()
	tr := &sandboxTree{root: root, upper: filepath.Join(root, "upper")}
	if err := os.MkdirAll(filepath.Join(tr.upper, "results"), 0755); err != nil {
		t.Fatal(err)
	}
	tr.secretBody = []byte("host-secret: root:sha512:0:0:root:/root:/bin/sh\n")
	tr.secretPath = filepath.Join(root, "secret")
	if err := os.WriteFile(tr.secretPath, tr.secretBody, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tr.upper, "results", "ok.bin"), []byte("legit-output"), 0644); err != nil {
		t.Fatal(err)
	}
	return tr
}

// readWithin calls the guard with base=upper (production passes base=mountpoint
// plus the disk/<cubebox>/upper prefix; the containment semantics are identical).
func readWithin(tr *sandboxTree, rel string, max int64) ([]byte, bool, error) {
	return ReadSandboxTreeFile(tr.upper, rel, max)
}

// mustNotBlock fails the test instead of hanging the suite.
func mustNotBlock(t *testing.T, d time.Duration, what string, fn func() error) {
	t.Helper()
	done := make(chan error, 1)
	go func() { done <- fn() }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("%s: unexpected error: %v", what, err)
		}
	case <-time.After(d):
		t.Fatalf("%s: blocked for %s (a guest-created special file must be refused, never waited on)", what, d)
	}
}

func TestA196_AbsoluteSymlinkToForeignFileIsRefused(t *testing.T) {
	tr := newSandboxTree(t)
	link := filepath.Join(tr.upper, "results", ".cds-exec-out")
	if err := os.Symlink(tr.secretPath, link); err != nil {
		t.Fatal(err)
	}

	data, exists, err := readWithin(tr, "results/.cds-exec-out", MaxSandboxFileBytes)
	if err == nil {
		t.Fatalf("symlink was followed: exists=%v data=%q", exists, data)
	}
	if exists {
		t.Error("a refused path must not report exists")
	}
	if len(data) != 0 {
		t.Fatalf("foreign file bytes returned through a guest symlink: %q", data)
	}
	if errors.Is(err, os.ErrNotExist) {
		t.Errorf("refusal must not be disguised as 'absent' (callers poll on that): %v", err)
	}
	if !errors.Is(err, ErrSandboxPathSymlink) && !errors.Is(err, ErrSandboxPathEscape) {
		t.Errorf("want a typed symlink/escape refusal, got %T: %v", err, err)
	}
	if !strings.Contains(err.Error(), "results/.cds-exec-out") {
		t.Errorf("error must name the offending guest path for the audit log, got %v", err)
	}
}

func TestA196_RelativeSymlinkAndInsideSymlinkAreRefused(t *testing.T) {
	// Strict no-follow: even a symlink that stays INSIDE upper is refused. The
	// copy-out path has no use for links, and "stays inside" is exactly the
	// judgement a guest can game.
	tr := newSandboxTree(t)
	for _, tc := range []struct{ name, target, linkRel string }{
		{"escape", "../../secret", "results/escape"},
		{"inside", "ok.bin", "results/inside"},
		{"inside-abs", filepath.Join(tr.upper, "results", "ok.bin"), "results/inside-abs"},
	} {
		if err := os.Symlink(tc.target, filepath.Join(tr.upper, tc.linkRel)); err != nil {
			t.Fatal(err)
		}
		data, exists, err := readWithin(tr, tc.linkRel, MaxSandboxFileBytes)
		if err == nil {
			t.Errorf("%s: symlink accepted (exists=%v data=%q)", tc.name, exists, data)
			continue
		}
		if !errors.Is(err, ErrSandboxPathSymlink) && !errors.Is(err, ErrSandboxPathEscape) {
			t.Errorf("%s: want typed symlink refusal, got %T: %v", tc.name, err, err)
		}
	}
}

func TestA196_SymlinkedAncestorDirectoryIsRefused(t *testing.T) {
	// <upper>/host -> /etc, then ask for host/hostname: the symlink is an
	// intermediate component, so a final-component-only check would miss it.
	tr := newSandboxTree(t)
	hostDir := filepath.Join(tr.root, "host")
	if err := os.WriteFile(hostDir+".file", []byte("hostname-bytes"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(hostDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(hostDir, "hostname"), []byte("hostname-bytes"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(hostDir, filepath.Join(tr.upper, "host")); err != nil {
		t.Fatal(err)
	}

	data, _, err := readWithin(tr, "host/hostname", MaxSandboxFileBytes)
	if err == nil {
		t.Fatalf("symlinked ancestor followed: %q", data)
	}
	if !errors.Is(err, ErrSandboxPathSymlink) && !errors.Is(err, ErrSandboxPathEscape) {
		t.Errorf("want typed symlink/escape refusal, got %T: %v", err, err)
	}
	if len(data) != 0 {
		t.Errorf("returned %q from outside upper", data)
	}
}

func TestA196_SymlinkedAncestorInsideUpperIsStillRefused(t *testing.T) {
	// No-follow means NO symlink, even one that never leaves upper: RESOLVE_BENEATH
	// alone would serve this, so this case is what makes RESOLVE_NO_SYMLINKS
	// load-bearing. The copy-out path has no legitimate use for links, and the
	// guest picks where a link points at read time.
	tr := newSandboxTree(t)
	if err := os.Symlink("results", filepath.Join(tr.upper, "alias")); err != nil {
		t.Fatal(err)
	}
	data, exists, err := readWithin(tr, "alias/ok.bin", MaxSandboxFileBytes)
	if err == nil {
		t.Fatalf("in-upper symlinked ancestor followed: exists=%v data=%q", exists, data)
	}
	if !errors.Is(err, ErrSandboxPathSymlink) {
		t.Errorf("want ErrSandboxPathSymlink for an in-upper link, got %T: %v", err, err)
	}
}

func TestA196_DotDotEscapeIsRefused(t *testing.T) {
	tr := newSandboxTree(t)
	for _, rel := range []string{"../secret", "results/../../secret", "/../../..//secret"} {
		data, _, err := readWithin(tr, rel, MaxSandboxFileBytes)
		if err == nil {
			t.Errorf("rel %q escaped upper and returned %d bytes", rel, len(data))
			continue
		}
		if !errors.Is(err, ErrSandboxPathEscape) {
			t.Errorf("rel %q: want ErrSandboxPathEscape, got %T: %v", rel, err, err)
		}
	}
	// A `..` that RESOLVE_BENEATH folds back INSIDE upper is containment doing
	// its job: the name reaches a path in the guest's own layer that is simply
	// absent — never the host file the name was pointed at.
	for _, rel := range []string{"results/../ok.bin/../secret", "results/./../secret"} {
		data, exists, err := readWithin(tr, rel, MaxSandboxFileBytes)
		if err != nil || exists || len(data) != 0 {
			t.Errorf("rel %q: want contained-and-absent, got exists=%v len=%d err=%v", rel, exists, len(data), err)
		}
	}
}

func TestA196_FifoIsRefusedWithoutBlocking(t *testing.T) {
	tr := newSandboxTree(t)
	fifo := filepath.Join(tr.upper, "results", "hang")
	if err := syscall.Mkfifo(fifo, 0644); err != nil {
		t.Skipf("mkfifo unavailable: %v", err)
	}
	// No writer is ever attached: an O_RDONLY open without O_NONBLOCK here is a
	// permanent block, which is what used to strand the deferred umount.
	var (
		data  []byte
		exist bool
		err   error
	)
	mustNotBlock(t, 5*time.Second, "fifo read-back", func() error {
		data, exist, err = readWithin(tr, "results/hang", MaxSandboxFileBytes)
		return nil
	})
	if err == nil {
		t.Fatalf("fifo accepted: exists=%v data=%q", exist, data)
	}
	if !errors.Is(err, ErrSandboxPathNotRegular) {
		t.Errorf("want ErrSandboxPathNotRegular for a fifo, got %T: %v", err, err)
	}
	if !strings.Contains(err.Error(), "regular") {
		t.Errorf("error must name the required file type, got %v", err)
	}
}

func TestA196_SocketAndDeviceShapedFilesAreRefused(t *testing.T) {
	tr := newSandboxTree(t)
	sock := filepath.Join(tr.upper, "results", "sock")
	ln, err := net.Listen("unix", sock)
	if err != nil {
		t.Skipf("unix socket unavailable: %v", err)
	}
	defer ln.Close()

	data, _, err := readWithin(tr, "results/sock", MaxSandboxFileBytes)
	if err == nil {
		t.Fatalf("unix socket accepted: %q", data)
	}
	if !errors.Is(err, ErrSandboxPathNotRegular) {
		t.Errorf("want ErrSandboxPathNotRegular for a socket, got %T: %v", err, err)
	}
}

func TestA196_CharDeviceIsRefused(t *testing.T) {
	// A /dev/zero-shaped node is the unbounded-read case from the ledger. It
	// cannot be created without CAP_MKNOD, so this stays skip-not-fail; the
	// same IsRegular() gate covers it and the socket/fifo cases pin it live.
	const charDevZero = int(1<<8 | 3) // makedev(1,3) = /dev/zero
	if os.Geteuid() != 0 {
		t.Skip("mknod needs CAP_MKNOD (root); irregular-file gate covered by fifo+socket tests")
	}
	tr := newSandboxTree(t)
	dev := filepath.Join(tr.upper, "results", "zero")
	if err := syscall.Mknod(dev, syscall.S_IFCHR|0666, charDevZero); err != nil {
		t.Skipf("mknod /dev/zero unavailable: %v", err)
	}
	data, _, err := readWithin(tr, "results/zero", MaxSandboxFileBytes)
	if err == nil {
		t.Fatalf("char device accepted: %d bytes", len(data))
	}
	if !errors.Is(err, ErrSandboxPathNotRegular) {
		t.Errorf("want ErrSandboxPathNotRegular for a char device, got %T: %v", err, err)
	}
}

func TestA196_RegularFileStillReadsAndBoundComesFromFstat(t *testing.T) {
	tr := newSandboxTree(t)

	data, exists, err := readWithin(tr, "results/ok.bin", MaxSandboxFileBytes)
	if err != nil || !exists || string(data) != "legit-output" {
		t.Fatalf("legit file broke: exists=%v data=%q err=%v", exists, data, err)
	}
	// A leading slash (the shape vm_path arrives in) must stay inside upper.
	if data, exists, err = readWithin(tr, "/results/ok.bin", MaxSandboxFileBytes); err != nil || !exists || string(data) != "legit-output" {
		t.Fatalf("absolute vm_path broke: exists=%v err=%v", exists, err)
	}
	// A `..` that resolves back inside upper stays readable (containment, not
	// lexical purity — the kernel decides).
	if data, exists, err = readWithin(tr, "results/../results/ok.bin", MaxSandboxFileBytes); err != nil || !exists {
		t.Fatalf("in-upper .. broke: exists=%v err=%v", exists, err)
	}

	// Oversize: a sparse file whose fstat size is over the bound must be
	// refused from the fd, before any bytes are copied.
	big := filepath.Join(tr.upper, "results", "big.bin")
	f, err := os.Create(big)
	if err != nil {
		t.Fatal(err)
	}
	if err := f.Truncate(4096); err != nil {
		t.Fatal(err)
	}
	f.Close()
	if _, _, err := readWithin(tr, "results/big.bin", 4095); !errors.Is(err, ErrSandboxFileTooLarge) {
		t.Errorf("want ErrSandboxFileTooLarge, got %T: %v", err, err)
	}
	if data, exists, err := readWithin(tr, "results/big.bin", 4096); err != nil || !exists || len(data) != 4096 {
		t.Errorf("file exactly at the bound should read fully: exists=%v len=%d err=%v", exists, len(data), err)
	}

	// Absent and directory keep the poll-friendly (nil, false, nil) shape.
	if data, exists, err := readWithin(tr, "results/nope.bin", MaxSandboxFileBytes); err != nil || exists || data != nil {
		t.Errorf("absent path: exists=%v data=%v err=%v, want false/nil/nil", exists, data, err)
	}
	if data, exists, err := readWithin(tr, "results", MaxSandboxFileBytes); err != nil || exists || data != nil {
		t.Errorf("directory: exists=%v data=%v err=%v, want false/nil/nil", exists, data, err)
	}
	// An empty rel / "." is the upper dir itself, not a read.
	if _, _, err := readWithin(tr, ".", MaxSandboxFileBytes); err != nil {
		t.Errorf("rel %q: unexpected error %v", ".", err)
	}
}

func TestA196_SentinelErrorsAreDistinct(t *testing.T) {
	// The control plane distinguishes "guest tried to escape" (audit + refuse
	// the session) from "not there yet" (keep polling), so the types must not
	// collapse into one another or into os.ErrNotExist.
	for _, e := range []error{ErrSandboxPathEscape, ErrSandboxPathSymlink, ErrSandboxPathNotRegular, ErrSandboxFileTooLarge} {
		if e == nil {
			t.Fatal("nil sentinel")
		}
		if errors.Is(e, os.ErrNotExist) || errors.Is(e, fs.ErrNotExist) {
			t.Errorf("%v must not be an ErrNotExist (callers poll on that)", e)
		}
	}
	for _, pair := range [][2]error{
		{ErrSandboxPathEscape, ErrSandboxPathSymlink},
		{ErrSandboxPathEscape, ErrSandboxPathNotRegular},
		{ErrSandboxPathSymlink, ErrSandboxPathNotRegular},
		{ErrSandboxPathNotRegular, ErrSandboxFileTooLarge},
	} {
		if errors.Is(pair[0], pair[1]) || errors.Is(pair[1], pair[0]) {
			t.Errorf("sentinels %v / %v must stay distinguishable", pair[0], pair[1])
		}
	}
}

// TestA196_ProductionPathGoesThroughGuard is the wiring lock: the unit tests
// above exercise the guard directly (a loop mount needs root), so this pins
// that ReadSandboxFile actually routes guest paths through it instead of
// re-opening them by path — the exact shape the finding is about.
func TestA196_ProductionPathGoesThroughGuard(t *testing.T) {
	src := packageSource(t)
	body := functionBody(src, "func (r *Plugin) ReadSandboxFile(")
	if body == "" {
		t.Fatal("ReadSandboxFile not found in package source")
	}
	if !strings.Contains(body, "ReadSandboxTreeFile(") {
		t.Error("ReadSandboxFile must resolve guest paths through ReadSandboxTreeFile (openat2 RESOLVE_BENEATH|NO_SYMLINKS)")
	}
	for _, banned := range []string{"os.ReadFile(", "os.Lstat(", "os.Open(", "filepath.Join(mountPoint, RootfsUpperSubdir"} {
		if strings.Contains(body, banned) {
			t.Errorf("ReadSandboxFile must not touch guest paths with %s — resolution and read must happen on the guarded fd", banned)
		}
	}
	helper := functionBody(src, "func ReadSandboxTreeFile(")
	if helper == "" {
		t.Fatal("ReadSandboxTreeFile missing from the package source")
	}
	for _, want := range []string{"IsRegular()", "fstat", "LimitReader"} {
		if !strings.Contains(helper, want) {
			t.Errorf("guard must use %s", want)
		}
	}
	// The contained open lives in a build-tagged file (openat2 is Linux-only);
	// it must pin all four resolve flags and O_NONBLOCK.
	for _, want := range []string{"RESOLVE_BENEATH", "RESOLVE_NO_SYMLINKS", "RESOLVE_NO_MAGICLINKS", "RESOLVE_NO_XDEV", "O_NONBLOCK"} {
		if !strings.Contains(src, want) {
			t.Errorf("the contained open must pin %s", want)
		}
	}
}

// packageSource concatenates the non-test Go sources of this package so the
// wiring lock does not care which file a function ended up in.
func packageSource(t *testing.T) string {
	t.Helper()
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	var sb strings.Builder
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		sb.Write(b)
	}
	return sb.String()
}

// functionBody returns the source of the named top-level function, from its
// signature to the first column-0 closing brace.
func functionBody(src, signature string) string {
	i := strings.Index(src, signature)
	if i < 0 {
		return ""
	}
	rest := src[i:]
	if j := strings.Index(rest, "\n}\n"); j >= 0 {
		return rest[:j+3]
	}
	return rest
}
