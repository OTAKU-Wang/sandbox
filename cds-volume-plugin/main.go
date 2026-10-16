// cds-volume-plugin — CDS 加密卷插件 (SS-10 CB-03).
//
// 数据进域侧 at-rest SM4-GCM 加密卷插件：Cubelet VolumePlugin 协议
// （Attach/Detach/Create/Destroy，proto 见 gen/）+ CDS 扩展数据通道。
// 铁律：禁伪造 / fail-closed —— 解密失败即错误，绝无明文回退。
package main

import (
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
		if err := serveCmd(); err != nil {
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

func serveCmd() error {
	cfg := LoadConfig()
	if err := cfg.Validate(); err != nil {
		return err
	}
	c, err := NewCipher(cfg)
	if err != nil {
		return err
	}
	if cfg.Backend == "aes_fallback" {
		// M-01 honest degradation: the GM SM4 path is inactive.
		log.Printf("WARNING: CDS_SM4_AEAD_BACKEND=aes_fallback — at-rest cipher is AES-128-GCM (non-GM, dev/test). Production MUST set CDS_SM4_AEAD_BACKEND=tongsuo (M-01 / spec §2.9).")
	}
	if err := RunSelfTest(c); err != nil {
		// spec CB-03 step 1 / §2.9: self-test failure = startup failure.
		return fmt.Errorf("boot self-test failed, refusing to serve (fail-closed): %w", err)
	}
	log.Printf("backend=%s node=%s volume_root=%s socket=%s", c.Name(), cfg.NodeID, cfg.VolumeRoot, cfg.SocketPath)
	p := NewPlugin(cfg, c, NewKMSClient(cfg.KMSBaseURL, cfg.KMSToken))
	return Serve(cfg, p)
}
