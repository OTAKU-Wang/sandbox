package vol_test

import . "cds-volume-plugin/internal/vol"

// Doc↔code parity gates for the operator-facing deployment face (A175 + the crypto_posture
// disclosure leg). Every expected value is DERIVED from the code/Dockerfile at run time
// (reflection over CryptoPosture, regex over config.go and Dockerfile) so the README cannot
// drift from what the binary actually reads or emits — prose is never pinned, only
// machine-consumed names.

import (
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
)

func a175dReadme(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(pluginRoot(t), "README.md"))
	if err != nil {
		t.Fatalf("read README.md: %v", err)
	}
	return string(b)
}

// a175dEnvKeys lists every CDS_* variable config.go actually reads, resolving the
// `const Env… = "CDS_…"` indirection the newer knobs use.
func a175dEnvKeys(t *testing.T) []string {
	t.Helper()
	src, err := os.ReadFile("config.go")
	if err != nil {
		t.Fatalf("read config.go: %v", err)
	}
	s := string(src)
	consts := map[string]string{}
	for _, m := range regexp.MustCompile(`(?m)^const\s+(\w+)\s*=\s*"(CDS_[A-Z0-9_]+)"`).FindAllStringSubmatch(s, -1) {
		consts[m[1]] = m[2]
	}
	seen := map[string]bool{}
	for _, m := range regexp.MustCompile(`env(?:Str|Int64|Bool)\(\s*(?:"(CDS_[A-Z0-9_]+)"|(Env[A-Za-z0-9_]+))`).FindAllStringSubmatch(s, -1) {
		name := m[1]
		if name == "" {
			name = consts[m[2]]
		}
		if name == "" {
			t.Fatalf("config.go reads env through %s, which does not resolve to a CDS_* name", m[2])
		}
		seen[name] = true
	}
	keys := make([]string, 0, len(seen))
	for k := range seen {
		keys = append(keys, k)
	}
	if len(keys) < 10 {
		t.Fatalf("only %d env keys parsed out of config.go — the extraction is broken, not the README", len(keys))
	}
	sort.Strings(keys)
	return keys
}

func TestA175D_EveryEnvKnobIsDocumented(t *testing.T) {
	readme := a175dReadme(t)
	for _, k := range a175dEnvKeys(t) {
		if !strings.Contains(readme, "`"+k+"`") {
			t.Errorf("%s is read by config.LoadConfig but has no README row: an operator cannot "+
				"find the knob (and a fail-closed default reads as a bug)", k)
		}
	}
}

// The at-rest crypto posture is what a deploy gate must read to refuse a non-GM node;
// its field names come from the struct, so a renamed json tag reddens the doc gate.
func TestA175D_CryptoPostureFieldsAreDocumented(t *testing.T) {
	readme := a175dReadme(t)
	if !strings.Contains(readme, "crypto_posture=") {
		t.Fatal("README never names the crypto_posture= boot record: the machine-readable GM/降级 " +
			"disclosure (the only field a deploy gate can read) is undocumented")
	}
	typ := reflect.TypeOf(CryptoPosture{})
	for i := 0; i < typ.NumField(); i++ {
		name := strings.Split(typ.Field(i).Tag.Get("json"), ",")[0]
		if name == "" || name == "-" {
			name = typ.Field(i).Name
		}
		if !strings.Contains(readme, name) {
			t.Errorf("crypto_posture field %q is not documented", name)
		}
	}
}

// A175: the shipped config default for the GM helper is a BARE name, resolved by
// exec.LookPath against PATH, and the helper receives the plaintext DEK on stdin. Until
// config.Validate() refuses it (NEEDS-OWNER, config.go is a closed face), the operator doc
// must carry the rule and name the absolute production value the image uses — read out of
// the Dockerfile here, not restated as a literal.
func TestA175D_HelperPathRuleIsDocumented(t *testing.T) {
	dockerfile, err := os.ReadFile(filepath.Join(pluginRoot(t), "Dockerfile"))
	if err != nil {
		t.Fatalf("read Dockerfile: %v", err)
	}
	m := regexp.MustCompile(`(?m)^\s*CDS_VOLUME_PLUGIN_SM4_HELPER=(\S+)$`).FindStringSubmatch(string(dockerfile))
	if m == nil {
		t.Fatal("Dockerfile does not pin CDS_VOLUME_PLUGIN_SM4_HELPER")
	}
	pinned := strings.TrimSuffix(m[1], `\`)
	readme := a175dReadme(t)
	if !strings.Contains(readme, pinned) {
		t.Errorf("README never names the absolute helper path %s the image ships: the bare config "+
			"default is PATH-resolved, and PATH-writable directories exist on nodes — whoever starts the "+
			"plugin outside the image must set an absolute path (A175)", pinned)
	}
	if !regexp.MustCompile("(?m)^\\| `CDS_VOLUME_PLUGIN_SM4_HELPER`.*绝对").MatchString(readme) {
		t.Fatal("the CDS_VOLUME_PLUGIN_SM4_HELPER row must state the 绝对路径 requirement (A175): " +
			"a relative default is resolved against PATH and the helper is fed the DEK on stdin")
	}
}
