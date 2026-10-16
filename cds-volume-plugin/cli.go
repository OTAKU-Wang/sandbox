package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
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
func runCLI(args []string) int {
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
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	if cfg.Backend == "aes_fallback" {
		fmt.Fprintf(os.Stderr, "WARNING: CDS_SM4_AEAD_BACKEND=aes_fallback (M-01: non-GM dev/test fallback, not SM4)\n")
	}
	if err := RunSelfTest(c); err != nil {
		fmt.Fprintln(os.Stderr, "self-test failed (fail-closed):", err)
		return 3
	}
	p := NewPlugin(cfg, c, NewKMSClient(cfg.KMSBaseURL, cfg.KMSToken))
	ctx := context.Background()
	jsonLine := func(v any) int {
		b, _ := json.Marshal(v)
		fmt.Println(string(b))
		return 0
	}
	fail := func(code int, err error) int {
		fmt.Fprintln(os.Stderr, "error:", err)
		return code
	}

	switch args[0] {
	case "selftest":
		fmt.Println(`{"selftest":"pass","cipher":"` + c.Name() + `"}`)
		return 0
	case "parse-annotation":
		if len(args) != 2 {
			return usage()
		}
		if err := parseAnnotationCmd(args[1]); err != nil {
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
			data, err := os.ReadFile(*dataFile)
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

func parseAnnotationCmd(payload string) error {
	var raw []map[string]any
	if err := json.Unmarshal([]byte(payload), &raw); err != nil {
		return fmt.Errorf("annotation must be a JSON array: %w", err)
	}
	out := make([]ParsedAnnotation, 0, len(raw))
	for _, e := range raw {
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
			return fmt.Errorf("annotation entry missing name/driver: %v", e)
		}
		out = append(out, na)
	}
	b, _ := json.MarshalIndent(out, "", "  ")
	fmt.Println(string(b))
	return nil
}
