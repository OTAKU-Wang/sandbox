package vol

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
)

// CLI surface (no gRPC server needed) — used by integration tests and ops:
//
//	cds-volume-plugin selftest
//	cds-volume-plugin ctl create  --volume-id V --name N
//	cds-volume-plugin ctl put     --volume-id V --data-file F [--aad A]
//	cds-volume-plugin ctl attach  --volume-id V --base-dir B --sandbox-id S --private-data P [--ref-count N]
//	cds-volume-plugin ctl detach  --volume-id V --base-dir B --ref-count 0
//	cds-volume-plugin ctl destroy --volume-id V
//	cds-volume-plugin ctl info    --volume-id V
//	cds-volume-plugin parse-annotation ANNOTATION_JSON
//
// ctl commands exit 0 on success and print a JSON result line; every
// fail-closed condition is a non-zero exit with the reason on stderr.
// Encoder marshals one CLI result. It is a parameter rather than a spelled-out
// json.Marshal so the Encoder's own error has a route to the exit code (A202⑥).
type Encoder func(v any) ([]byte, error)

// EmitJSON writes exactly one JSON document and hands back any encoding or
// write failure: an empty line on stdout with exit 0 is what a scripted deploy
// then reads as success.
func EmitJSON(w io.Writer, enc Encoder, v any) error {
	b, err := enc(v)
	if err != nil {
		return fmt.Errorf("encode JSON result: %w", err)
	}
	if _, err := w.Write(append(b, '\n')); err != nil {
		return fmt.Errorf("write JSON result: %w", err)
	}
	return nil
}

// ReadVolumeData returns --data-file content bounded by maxBytes. The size comes
// from the directory entry, so an oversized blob is refused before any of it is
// read into this process (which runs as root) — the cap in Config.Validate only
// bounds what PutVolumeData accepts afterwards (A202②).
func ReadVolumeData(path string, maxBytes int64) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !fi.Mode().IsRegular() {
		return nil, fmt.Errorf("data file %s is not a regular file (mode %s)", path, fi.Mode().Type())
	}
	if fi.Size() > maxBytes {
		return nil, fmt.Errorf("data file %s holds %d bytes, over the %d-byte CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES cap (refused before reading)",
			path, fi.Size(), maxBytes)
	}
	data, err := io.ReadAll(io.LimitReader(f, maxBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > maxBytes {
		return nil, fmt.Errorf("data file %s exceeded the %d-byte CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES cap while being read",
			path, maxBytes)
	}
	return data, nil
}

func RunCLI(args []string) int { return RunCLIWith(args, json.Marshal) }

func RunCLIWith(args []string, enc Encoder) int {
	if len(args) < 1 {
		return usage()
	}
	cfg := LoadConfig()
	if err := cfg.Validate(); err != nil {
		fmt.Fprintln(os.Stderr, "config:", err)
		return 2
	}
	c, err := NewCipher(cfg)
	if err != nil {
		// A backend that cannot be constructed is a config defect (A202③: an
		// unusable helper budget used to surface here as a crypto failure and send
		// the operator to the wrong component).
		fmt.Fprintln(os.Stderr, "config:", err)
		return 2
	}
	posture := NewCryptoPosture(cfg.Backend, c.Name())
	if posture.Degraded {
		fmt.Fprintf(os.Stderr, "WARNING: CDS_SM4_AEAD_BACKEND=%s (M-01: non-GM dev/test fallback, not SM4)\n", cfg.Backend)
	}
	line, err := CryptoPostureLine(posture, json.Marshal)
	if err != nil {
		fmt.Fprintln(os.Stderr, "config: cannot disclose the at-rest crypto posture (fail-closed):", err)
		return 2
	}
	fmt.Fprintln(os.Stderr, line)
	if err := RunSelfTest(c); err != nil {
		fmt.Fprintln(os.Stderr, "self-test failed (fail-closed):", err)
		return 3
	}
	kms, err := NewKMSClientFromConfig(cfg)
	if err != nil {
		fmt.Fprintln(os.Stderr, "kms transport:", err)
		return 2
	}
	p := NewPlugin(cfg, c, kms)
	ctx := context.Background()
	fail := func(code int, err error) int {
		fmt.Fprintln(os.Stderr, "error:", err)
		return code
	}
	jsonLine := func(v any) int {
		if err := EmitJSON(os.Stdout, enc, v); err != nil {
			return fail(5, err)
		}
		return 0
	}

	switch args[0] {
	case "selftest":
		return jsonLine(struct {
			SelfTest string `json:"selftest"`
			CryptoPosture
		}{"pass", posture})
	case "parse-annotation":
		if len(args) != 2 {
			return usage()
		}
		if err := ParseAnnotationCmd(args[1]); err != nil {
			return fail(2, err)
		}
		return 0
	case "ctl":
		if len(args) < 2 {
			return usage()
		}
		fs := flag.NewFlagSet(args[1], flag.ContinueOnError)
		fs.SetOutput(os.Stderr)
		vid := fs.String("volume-id", "", "")
		name := fs.String("name", "", "")
		dataFile := fs.String("data-file", "", "")
		aad := fs.String("aad", "", "")
		baseDir := fs.String("base-dir", "", "")
		sandboxID := fs.String("sandbox-id", "cli", "")
		pd := fs.String("private-data", "", "")
		refCount := fs.Int("ref-count", 0, "")
		if err := fs.Parse(args[2:]); err != nil {
			return usage()
		}
		switch args[1] {
		case "create":
			out, err := p.CreateVolume(ctx, *vid, *name)
			if err != nil {
				return fail(4, err)
			}
			return jsonLine(map[string]string{"private_data": out, "volume_id": *vid})
		case "put":
			if *dataFile == "" {
				return usage()
			}
			data, err := ReadVolumeData(*dataFile, cfg.MaxVolumeBytes)
			if err != nil {
				return fail(4, err)
			}
			n, idem, err := p.PutVolumeData(ctx, *vid, *aad, data)
			if err != nil {
				return fail(4, err)
			}
			return jsonLine(map[string]any{"volume_id": *vid, "data_size_bytes": n, "idempotent_retry": idem})
		case "attach":
			hostPath, meta, err := p.Attach(ctx, *vid, *baseDir, *sandboxID, *pd, int64(*refCount))
			if err != nil {
				return fail(4, err)
			}
			return jsonLine(map[string]any{"host_path": hostPath, "metadata": meta})
		case "detach":
			if err := p.Detach(ctx, *vid, *baseDir, int64(*refCount)); err != nil {
				return fail(4, err)
			}
			return jsonLine(map[string]string{"volume_id": *vid, "detached": "true"})
		case "destroy":
			if err := p.Destroy(ctx, *vid); err != nil {
				return fail(4, err)
			}
			return jsonLine(map[string]string{"volume_id": *vid, "destroyed": "true"})
		case "info":
			m, err := p.GetVolumeInfo(*vid)
			if err != nil {
				return fail(4, err)
			}
			return jsonLine(m)
		default:
			return usage()
		}
	default:
		return usage()
	}
}

func usage() int {
	fmt.Fprintln(os.Stderr, "usage: cds-volume-plugin <selftest|ctl <create|put|attach|detach|destroy|info>|parse-annotation JSON>")
	return 2
}

// ParsedAnnotation is one entry of the plugin-volume-sources annotation
// (spec §2.4): [{name, driver, private_data}]. private_data is surfaced
// both as the raw string and, when it is a JSON object, as a parsed map.
type ParsedAnnotation struct {
	Name        string         `json:"name"`
	Driver      string         `json:"driver"`
	PrivateData string         `json:"private_data"`
	ParsedData  map[string]any `json:"parsed_data,omitempty"`
	CDS         bool           `json:"is_cds_driver"`
}

func ParseAnnotationCmd(payload string) error {
	return parseAnnotation(payload, func(v any) ([]byte, error) { return json.MarshalIndent(v, "", "  ") })
}

func parseAnnotation(payload string, enc Encoder) error {
	var raw []map[string]any
	if err := json.Unmarshal([]byte(payload), &raw); err != nil {
		return fmt.Errorf("annotation must be a JSON array: %w", err)
	}
	out := make([]ParsedAnnotation, 0, len(raw))
	for i, e := range raw {
		na := ParsedAnnotation{}
		if s, ok := e["name"].(string); ok {
			na.Name = s
		}
		if s, ok := e["driver"].(string); ok {
			na.Driver = s
			na.CDS = s == PluginName
		}
		if s, ok := e["private_data"].(string); ok {
			na.PrivateData = s
			var m map[string]any
			if json.Unmarshal([]byte(s), &m) == nil {
				na.ParsedData = m
			}
		}
		if na.Name == "" || na.Driver == "" {
			// The entry itself is never echoed: private_data carries the DEK
			// reference, and this error is what operators and callers capture (A182).
			missing := make([]string, 0, 2)
			if na.Name == "" {
				missing = append(missing, "name")
			}
			if na.Driver == "" {
				missing = append(missing, "driver")
			}
			return fmt.Errorf("annotation entry %d is missing %s", i, strings.Join(missing, "/"))
		}
		out = append(out, na)
	}
	return EmitJSON(os.Stdout, enc, out)
}
