// cds-volume-plugin — CDS 加密卷插件 (SS-10 CB-03).
//
// 数据进域侧 at-rest SM4-GCM 加密卷插件：Cubelet VolumePlugin 协议
// （Attach/Detach/Create/Destroy，proto 见 gen/）+ CDS 扩展数据通道。
// 铁律：禁伪造 / fail-closed —— 解密失败即错误，绝无明文回退。
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: cds-volume-plugin <serve|selftest|ctl|parse-annotation>")
		os.Exit(2)
	}
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	log.SetPrefix("cds-volume-plugin: ")

	switch os.Args[1] {
	case "serve":
		if err := serveCmd(os.Args[2:]); err != nil {
			log.Fatal(err)
		}
	case "selftest":
		os.Exit(runCLI([]string{"selftest"}))
	case "ctl", "parse-annotation":
		os.Exit(runCLI(os.Args[1:]))
	default:
		fmt.Fprintln(os.Stderr, "unknown command:", os.Args[1])
		os.Exit(2)
	}
}

// serveOptions carries what the operator passed on the serve command line.
type serveOptions struct {
	// AllowInsecureKMS knowingly accepts a cleartext http KMS endpoint on a
	// non-loopback host (A179). Same switch as the env key, for operators.
	AllowInsecureKMS bool
}

func parseServeFlags(args []string) (serveOptions, error) {
	var opts serveOptions
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	fs.BoolVar(&opts.AllowInsecureKMS, "allow-insecure-kms", false,
		"accept a cleartext http KMS endpoint on a non-loopback host (dev only; logged as a WARNING)")
	if err := fs.Parse(args); err != nil {
		return serveOptions{}, err
	}
	return opts, nil
}

// CryptoPosture is the machine-readable at-rest crypto disclosure. The
// aes_fallback backend is a degrade — the at-rest cipher is then not the GM/T
// SM4-GCM this product specifies (M-01) — and honest degradation means a deploy
// gate must be able to read that from a field instead of scraping a human
// WARNING line out of the boot log (A202⑧).
type CryptoPosture struct {
	AEADBackend        string `json:"aead_backend"`
	CipherName         string `json:"cipher"`
	GMCompliant        bool   `json:"gm_compliant"`
	Degraded           bool   `json:"degraded"`
	Disclosure         string `json:"disclosure"`
	ProductionRequires string `json:"production_requires,omitempty"`
}

func newCryptoPosture(backend, cipherName string) CryptoPosture {
	if backend == "tongsuo" {
		return CryptoPosture{
			AEADBackend: backend,
			CipherName:  cipherName,
			GMCompliant: true,
			Disclosure:  "at-rest AEAD is SM4-GCM through the libtongsuo helper (GM/T)",
		}
	}
	return CryptoPosture{
		AEADBackend:        backend,
		CipherName:         cipherName,
		GMCompliant:        false,
		Degraded:           true,
		Disclosure:         "at-rest AEAD is " + cipherName + ", NOT the GM/T SM4-GCM this product specifies (M-01): dev/test fallback, data at rest is protected by a non-national cipher",
		ProductionRequires: "CDS_SM4_AEAD_BACKEND=tongsuo",
	}
}

// cryptoPostureLine renders the posture as one grep-able boot-log record.
func cryptoPostureLine(p CryptoPosture, enc encoder) (string, error) {
	b, err := enc(p)
	if err != nil {
		return "", fmt.Errorf("encode crypto posture: %w", err)
	}
	return "crypto_posture=" + string(b), nil
}

func serveCmd(args []string) error {
	flags, err := parseServeFlags(args)
	if err != nil {
		return err
	}
	cfg := LoadConfig()
	if flags.AllowInsecureKMS {
		cfg.KMSAllowInsecure = true
	}
	if err := cfg.Validate(); err != nil {
		return err
	}
	// Transport validation is part of boot: a cleartext or unparseable KMS
	// endpoint refuses to start rather than sending DEKs in the clear (A179).
	kms, err := NewKMSClientFromConfig(cfg)
	if err != nil {
		return err
	}
	c, err := NewCipher(cfg)
	if err != nil {
		// A backend that cannot be constructed is a config defect, and the message
		// must say so rather than blaming the crypto path (A202③).
		return fmt.Errorf("cipher config: %w", err)
	}
	posture := newCryptoPosture(cfg.Backend, c.Name())
	if posture.Degraded {
		// M-01 honest degradation: the GM SM4 path is inactive.
		log.Printf("WARNING: CDS_SM4_AEAD_BACKEND=%s — at-rest cipher is AES-128-GCM (non-GM, dev/test). Production MUST set CDS_SM4_AEAD_BACKEND=tongsuo (M-01 / spec §2.9).", cfg.Backend)
	}
	// A degrade this process cannot disclose is one it must not serve under.
	line, err := cryptoPostureLine(posture, json.Marshal)
	if err != nil {
		return fmt.Errorf("cannot disclose the at-rest crypto posture (fail-closed): %w", err)
	}
	log.Print(line)
	if err := RunSelfTest(c); err != nil {
		// spec CB-03 step 1 / §2.9: self-test failure = startup failure.
		return fmt.Errorf("boot self-test failed, refusing to serve (fail-closed): %w", err)
	}
	log.Printf("backend=%s node=%s volume_root=%s socket=%s", c.Name(), cfg.NodeID, cfg.VolumeRoot, cfg.SocketPath)
	log.Printf("kms_endpoint=%s kms_transport=%s", cfg.KMSBaseURL, kms.TransportMode())
	p := NewPlugin(cfg, c, kms)
	return Serve(cfg, p)
}
