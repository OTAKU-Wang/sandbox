package main

// A205④ (supply-chain traceability leg of the image finding): all four FROM lines floated
// on mutable tags, so two builds of the SAME commit — of the image that ships the C helper
// which is fed the plaintext DEK on stdin — could compile against different upstream bits,
// with nothing in the repo recording which.
//
// This gate pins the SHAPE of the pin, never a value: an upstream re-push of a tag must NOT
// redden it (that would turn a legitimate re-resolution into a false failure). What it
// refuses is a build that cannot state, from this file alone, exactly which bytes it started
// from. Expected values are DERIVED at run time from the sibling artifacts (Dockerfile ↔
// README.md ↔ go.mod), never restated as literals here — the repo's gate style.

import (
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

var (
	a205cFromRe   = regexp.MustCompile(`(?i)^FROM\s+(\S+)`)
	a205cStageRe  = regexp.MustCompile(`(?i)^FROM\s+\S+\s+AS\s+([A-Za-z0-9_.-]+)`)
	a205cPinnedRe = regexp.MustCompile(`^([A-Za-z0-9._:/-]+:[A-Za-z0-9._-]+)@sha256:([0-9a-f]{64})$`)
	a205cArgRe    = regexp.MustCompile(`(?m)^ARG\s+([A-Za-z_][A-Za-z0-9_]*)`)
	a205cDateRe   = regexp.MustCompile(`\d{4}-\d{2}-\d{2}`)
)

// a205cDockerfile reads the artifact under gate. go test runs with the package directory as
// CWD, which is exactly where the Dockerfile sits.
func a205cDockerfile(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile("Dockerfile")
	if err != nil {
		t.Fatalf("read Dockerfile: %v", err)
	}
	return string(b)
}

// a205cFromRefs returns the image reference of every active FROM line: comments are skipped,
// and a FROM that names an earlier build stage (`FROM tongsuo AS x`) is dropped, because a
// stage alias is not a base image pulled from a registry.
func a205cFromRefs(t *testing.T, dockerfile string) []string {
	t.Helper()
	stages := map[string]bool{}
	for _, line := range strings.Split(dockerfile, "\n") {
		s := strings.TrimSpace(line)
		if strings.HasPrefix(s, "#") {
			continue
		}
		if m := a205cStageRe.FindStringSubmatch(s); m != nil {
			stages[strings.ToLower(m[1])] = true
		}
	}
	var refs []string
	for _, line := range strings.Split(dockerfile, "\n") {
		s := strings.TrimSpace(line)
		if strings.HasPrefix(s, "#") {
			continue
		}
		m := a205cFromRe.FindStringSubmatch(s)
		if m == nil {
			continue
		}
		ref := m[1]
		if strings.EqualFold(ref, "scratch") || stages[strings.ToLower(ref)] {
			continue
		}
		refs = append(refs, ref)
	}
	if len(refs) == 0 {
		t.Fatal("Dockerfile has no base-image FROM line: this gate cannot see what the image starts from")
	}
	return refs
}

// TestA205C_EveryBaseImageIsDigestPinned: each base image must arrive as
// `<repo>:<tag>@sha256:<64 hex>`. The tag is kept beside the digest on purpose (an auditable
// human name for the bytes), and a build-arg-interpolated image name is refused: the digest
// was resolved for ONE concrete tag, so letting `--build-arg` rewrite the name while keeping
// the digest is exactly how a pin becomes a lie.
func TestA205C_EveryBaseImageIsDigestPinned(t *testing.T) {
	refs := a205cFromRefs(t, a205cDockerfile(t))
	for _, ref := range refs {
		if strings.Contains(ref, "${") {
			t.Fatalf("FROM %s resolves the base image from a build ARG: the digest recorded next to it "+
				"was resolved for one concrete tag, so `--build-arg` can point the same digest at different "+
				"bits and the pin stops meaning anything — the FROM line itself must carry @sha256: (A205④)", ref)
		}
		m := a205cPinnedRe.FindStringSubmatch(ref)
		if m == nil {
			t.Fatalf("FROM %s is not pinned to an immutable digest. Expected `<repo>:<tag>@sha256:<64 hex>`: "+
				"a floating tag means two builds of this commit can compile the plugin — and the helper that "+
				"receives the plaintext DEK — against different upstream bytes, and nothing records which "+
				"(A205④ supply-chain traceability)", ref)
		}
	}
	if len(refs) < 2 {
		t.Fatalf("expected the multi-stage plugin image to gate more than one FROM (%d seen): if stages were "+
			"dropped, this gate is looking at the wrong file", len(refs))
	}
}

// TestA205C_SameImageSameDigestAcrossStages: the three Go stages must not drift onto different
// digests of one tag — that would make "which compiler built this" stage-dependent.
func TestA205C_SameImageSameDigestAcrossStages(t *testing.T) {
	seen := map[string]string{}
	for _, ref := range a205cFromRefs(t, a205cDockerfile(t)) {
		m := a205cPinnedRe.FindStringSubmatch(ref)
		if m == nil {
			t.Fatalf("FROM %s is not `<repo>:<tag>@sha256:<64 hex>`, so the digests of one image cannot be "+
				"compared across stages (A205④)", ref)
		}
		if prev, ok := seen[m[1]]; ok && prev != m[2] {
			t.Fatalf("image %s is pinned to two different digests in one Dockerfile (%s… vs %s…): the build "+
				"is not reproducible from the file (A205④)", m[1], prev[:12], m[2][:12])
		}
		seen[m[1]] = m[2]
	}
}

// TestA205C_GoToolchainPinnedAndMatchesGoMod: the compiler that builds the plugin is itself a
// base image, so its pinned tag must satisfy the module's `go` directive. The required version
// is READ from go.mod, never restated here.
func TestA205C_GoToolchainPinnedAndMatchesGoMod(t *testing.T) {
	var goMod string
	if b, err := os.ReadFile("go.mod"); err != nil {
		t.Fatalf("read go.mod: %v", err)
	} else {
		goMod = string(b)
	}
	mm := regexp.MustCompile(`(?m)^go\s+(\d+)\.(\d+)`).FindStringSubmatch(goMod)
	if mm == nil {
		t.Fatal("cannot read the `go` directive out of go.mod: this gate would otherwise drift with it")
	}
	wantMajor, _ := strconv.Atoi(mm[1])
	wantMinor, _ := strconv.Atoi(mm[2])

	found := 0
	for _, ref := range a205cFromRefs(t, a205cDockerfile(t)) {
		m := a205cPinnedRe.FindStringSubmatch(ref)
		if m == nil || !strings.HasPrefix(m[1], "golang:") {
			continue
		}
		found++
		tm := regexp.MustCompile(`^golang:(\d+)\.(\d+)`).FindStringSubmatch(m[1])
		if tm == nil {
			t.Fatalf("golang base image tag %q carries no readable major.minor: the toolchain口径 cannot be "+
				"checked against go.mod (A205④)", m[1])
		}
		gotMajor, _ := strconv.Atoi(tm[1])
		gotMinor, _ := strconv.Atoi(tm[2])
		if gotMajor < wantMajor || (gotMajor == wantMajor && gotMinor < wantMinor) {
			t.Fatalf("the pinned Go base image is %s but go.mod requires go %s.%s: the build would fail in a "+
				"way the pin was supposed to prevent", m[1], mm[1], mm[2])
		}
	}
	if found == 0 {
		t.Fatal("no digest-pinned golang base image in the Dockerfile: the compiler that builds this plugin is " +
			"not pinned, so the artifact cannot be attributed to known bits (A205④)")
	}
}

// TestA205C_DigestProvenanceIsRecorded: a pin nobody can re-derive is a faith statement. Every
// pinned reference must appear VERBATIM in README.md, on a line that also carries an acquisition
// date, and the README must document the re-fetch recipe (registry manifest endpoint + the header
// the digest comes from) so the next operator refreshes it from the registry instead of guessing.
func TestA205C_DigestProvenanceIsRecorded(t *testing.T) {
	readme, err := os.ReadFile("README.md")
	if err != nil {
		t.Fatalf("read README.md: %v", err)
	}
	doc := string(readme)
	lines := strings.Split(doc, "\n")

	seen := map[string]bool{}
	for _, ref := range a205cFromRefs(t, a205cDockerfile(t)) {
		if !a205cPinnedRe.MatchString(ref) {
			t.Fatalf("FROM %s is not digest-pinned, so there is no provenance to record (A205④)", ref)
		}
		seen[ref] = true
		found := false
		for _, line := range lines {
			if strings.Contains(line, ref) && a205cDateRe.MatchString(line) {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("README.md never records %s on a line with an acquisition date: the digest the image is "+
				"pinned to has no recorded 取值时间, so an audit cannot tell a stale pin from a fresh one (A205④)", ref)
		}
	}
	if len(seen) == 0 {
		t.Fatal("no pinned base image found to check provenance for")
	}

	if !strings.Contains(doc, "registry-1.docker.io/v2/library/") {
		t.Fatal("README.md does not name the registry manifest endpoint used to resolve a tag to a digest: a " +
			"refresh has to be re-derived by whoever needs it (A205④)")
	}
	if !strings.Contains(doc, "Docker-Content-Digest") {
		t.Fatal("README.md does not name the Docker-Content-Digest header: without the field the digest came " +
			"from, the recorded recipe is not reproducible (A205④)")
	}
}

// TestA205C_NoDanglingBuildArg: `ARG GO_VER` existed only to compose a floating tag. Leaving a
// build ARG behind after pinning would advertise a knob that changes nothing (or, worse, one that
// silently changes the image while the digest stays put), so every ARG must actually be consumed.
func TestA205C_NoDanglingBuildArg(t *testing.T) {
	dockerfile := a205cDockerfile(t)
	for _, name := range a205cArgRe.FindAllStringSubmatch(dockerfile, -1) {
		if !strings.Contains(dockerfile, "${"+name[1]+"}") {
			t.Fatalf("ARG %s is declared but never referenced: a build ARG that does nothing is a knob that "+
				"lies about the build (A205④ left this behind when the tags were replaced by digests)", name[1])
		}
	}
}
