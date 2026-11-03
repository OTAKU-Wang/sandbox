package vol_test

// Build-face gates for the national-crypto / deployment legs that no Python test can
// reach (A204①③, A204② Makefile half, A175 image half, A205② identity). The plugin is
// not wired into ci.yml, so `make test` plus these assertions are the strongest
// automated door the image build has. Every assertion here reads the artifact and
// DERIVES the expected value from its sibling artifact (sm4gcm/Makefile ↔ Dockerfile),
// so a future change to one side reddens the gate instead of silently splitting the口径.

import (
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

func a204cRead(t *testing.T, rel string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(pluginRoot(t), rel))
	if err != nil {
		t.Fatalf("read %s: %v", rel, err)
	}
	return string(b)
}

// a204cFinalStage returns the runtime stage (everything after the last FROM): a package
// installed in a build stage says nothing about what the shipped image contains.
func a204cFinalStage(t *testing.T, dockerfile string) string {
	t.Helper()
	i := strings.LastIndex(dockerfile, "\nFROM ")
	if i < 0 {
		t.Fatal("Dockerfile has no FROM stage")
	}
	return dockerfile[i:]
}

// A204②: `make test` must build the GM helper. The Go gates now refuse to pass without
// sm4gcm/sm4_gcm (plugin_test.go gmHelperPath, a204_test.go a204Helper), so a `test:`
// target that does not depend on `helper` would make the only automated door fail on a
// clean checkout instead of covering SM4-GCM.
func TestA204C_MakeTestTargetBuildsGMHelper(t *testing.T) {
	out, err := exec.Command("make", "-C", pluginRoot(t), "-n", "test").CombinedOutput()
	if err != nil {
		t.Fatalf("make -n test: %v\n%s", err, out)
	}
	if s := string(out); !strings.Contains(s, "sm4gcm") {
		t.Fatalf("`make test` does not build the SM4-GCM helper (no sm4gcm step in %q): the "+
			"fail-closed GM gates cannot run on a clean checkout and the national-crypto backend "+
			"stays uncovered by the only automated door (A204②)", strings.TrimSpace(s))
	}
}

// A204①: debian:bookworm-slim ships an EMPTY trust store, so an https KMS endpoint used
// to pass every build gate and then fail every DEK call with x509 unknown-authority.
func TestA204C_FinalImageCarriesTrustRoots(t *testing.T) {
	fs := a204cFinalStage(t, a204cRead(t, "Dockerfile"))
	if !strings.Contains(fs, "ca-certificates") {
		t.Fatal("final runtime stage installs no ca-certificates: the shipped image has no trust " +
			"anchors, so an https CDS_VOLUME_PLUGIN_KMS_BASE_URL fails every DEK call with " +
			"x509 \"certificate signed by unknown authority\" against a fail-closed KMS client (A204①)")
	}
	if !regexp.MustCompile(`test\s+-s\s+/etc/ssl/certs/ca-certificates\.crt`).MatchString(fs) {
		t.Fatal("the CA install is not asserted at build time: add `test -s " +
			"/etc/ssl/certs/ca-certificates.crt` so a slim base without roots fails the build (A204①)")
	}
}

// A204③: one libdir, not two口径. The expected directory is READ from sm4gcm/Makefile
// (the file that actually compiles the helper), never restated as a literal here.
func TestA204C_OneLibdirAcrossDockerfileAndHelperMakefile(t *testing.T) {
	mk := a204cRead(t, filepath.Join("sm4gcm", "Makefile"))
	m := regexp.MustCompile(`(?m)^TSLIB\s*:=\s*\$\(abspath\s+\$\(PREFIX\)/([A-Za-z0-9_-]+)\)`).
		FindStringSubmatch(mk)
	if m == nil {
		t.Fatal("cannot read the helper's Tongsuo libdir out of sm4gcm/Makefile (TSLIB := " +
			"$(abspath $(PREFIX)/<dir>)): this gate would otherwise drift with it")
	}
	helperLibdir := m[1]

	dockerfile := a204cRead(t, "Dockerfile")
	configure := regexp.MustCompile(`(?m)^\s*RUN \./Configure[^\n]*`).FindString(dockerfile)
	if configure == "" {
		t.Fatal("Dockerfile has no ./Configure line — cannot compare libdir口径")
	}
	if !strings.Contains(configure, "--libdir="+helperLibdir) {
		t.Fatalf("two libdir口径: sm4gcm/Makefile builds the helper against $(PREFIX)/%s but the "+
			"image configures Tongsuo without --libdir=%s (%s) — the existence check could pass while "+
			"the helper stage dies with an opaque cc error (A204③)", helperLibdir, helperLibdir,
			strings.TrimSpace(configure))
	}
	if strings.Contains(dockerfile, "/tongsuo/lib*/") {
		t.Fatal("Dockerfile still probes the Tongsuo install with a lib* wildcard: a wildcard pass " +
			"does not prove the directory the helper compiles against exists (A204③)")
	}
	if !strings.Contains(dockerfile, "/tongsuo/"+helperLibdir+"/libcrypto.a") {
		t.Fatalf("Dockerfile must assert the helper's own path exists: /tongsuo/%s/libcrypto.a", helperLibdir)
	}
}

// A175 (image half): the config DEFAULT for the helper is the bare name `sm4_gcm`, which
// exec.LookPath resolves anywhere on PATH — and the helper is handed the plaintext DEK on
// stdin. The image must therefore pin an absolute path and prove the file is there.
func TestA175C_ImagePinsHelperToAbsolutePath(t *testing.T) {
	dockerfile := a204cRead(t, "Dockerfile")
	m := regexp.MustCompile(`(?m)^\s*CDS_VOLUME_PLUGIN_SM4_HELPER=(\S+)$`).FindStringSubmatch(dockerfile)
	if m == nil {
		t.Fatal("image does not pin CDS_VOLUME_PLUGIN_SM4_HELPER: the shipped default would be the " +
			"bare config.go default, resolved against the host PATH (A175)")
	}
	pinned := strings.TrimSuffix(m[1], `\`)
	if !filepath.IsAbs(pinned) {
		t.Fatalf("A175: image pins the GM helper to %q — a relative name is resolved by exec.LookPath "+
			"against PATH, and PATH-writable directories are common on nodes; that binary would be run "+
			"with plugin privileges and handed the plaintext DEK on stdin", pinned)
	}
	fs := a204cFinalStage(t, dockerfile)
	if !strings.Contains(fs, "test -x "+pinned) {
		t.Fatalf("A175: no build-time `test -x %s` in the final stage: a mis-copied helper would only "+
			"surface as a crypto error on the first Seal()", pinned)
	}
}

// A205② (identity half): the plugin needs root (loop mount + chown root:cds) and refuses
// to serve without it unless explicitly opted out — the image has to say so instead of
// inheriting an identity by accident.
func TestA205C_FinalImageStatesPrivilegedIdentity(t *testing.T) {
	fs := a204cFinalStage(t, a204cRead(t, "Dockerfile"))
	if !regexp.MustCompile(`(?m)^USER\s+root\s*$`).MatchString(fs) {
		t.Fatal("A205②: final stage declares no USER. The plugin loop-mounts rootfs images and " +
			"chowns its socket to root:cds, and server.go EnforceSocketAccess refuses to serve as " +
			"non-root — the image must state the privileged identity explicitly")
	}
	if !strings.Contains(fs, `VOLUME ["/var/lib/cds/volumes"]`) {
		t.Fatal(`A205②: the ciphertext store must stay declared as VOLUME ["/var/lib/cds/volumes"] ` +
			`(it must never land in the container layer)`)
	}
}
