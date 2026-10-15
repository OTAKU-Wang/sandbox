package main

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Config is the plugin's environment configuration (SS-10 CB-03).
// Key matrix: spec §6.1 (CDS_CUBE_VOLUME_PLUGIN_SOCKET, CDS_SM4_AEAD_BACKEND)
// plus CDS_VOLUME_PLUGIN_* plugin-local keys.
type Config struct {
	// SocketPath is the unix domain socket the plugin serves (spec §6.1).
	SocketPath string
	// VolumeRoot is the at-rest ciphertext directory tree.
	VolumeRoot string
	// Backend selects the AEAD backend: "tongsuo" (libtongsuo helper, GM)
	// or "aes_fallback" (Go AES-128-GCM, dev/test only, loud warning).
	// Spec §6.1 default is aes_fallback; production MUST run tongsuo.
	Backend string
	// HelperPath is the sm4_gcm helper binary (tongsuo backend).
	HelperPath string
	// SubprocTimeout bounds every helper invocation (spec §2.9 subprocess timeout).
	SubprocTimeout time.Duration
	// KMSBaseURL is the CDS control-plane base URL for DEK registration.
	KMSBaseURL string
	// KMSToken is the service credential presented to the control plane.
	KMSToken string
	// NodeID identifies this node in logs/audit.
	NodeID string
	// MaxVolumeBytes bounds a single volume data blob.
	MaxVolumeBytes int64
}

func envStr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt64(key string, def int64) int64 {
	if v := os.Getenv(key); v != "" {
		n, err := strconv.ParseInt(v, 10, 64)
		if err == nil {
			return n
		}
	}
	return def
}

// LoadConfig reads CDS_* environment configuration.
func LoadConfig() Config {
	return Config{
		SocketPath:     envStr("CDS_CUBE_VOLUME_PLUGIN_SOCKET", "/run/cds-volume-plugin/cds.sock"),
		VolumeRoot:     envStr("CDS_VOLUME_PLUGIN_VOLUME_ROOT", "/var/lib/cds/volumes"),
		Backend:        envStr("CDS_SM4_AEAD_BACKEND", "aes_fallback"),
		HelperPath:     envStr("CDS_VOLUME_PLUGIN_SM4_HELPER", "sm4_gcm"),
		SubprocTimeout: time.Duration(envInt64("CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS", 10000)) * time.Millisecond,
		KMSBaseURL:     envStr("CDS_VOLUME_PLUGIN_KMS_BASE_URL", "http://127.0.0.1:8000"),
		KMSToken:       envStr("CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN", ""),
		NodeID:         envStr("CDS_VOLUME_PLUGIN_NODE_ID", "local"),
		MaxVolumeBytes: envInt64("CDS_VOLUME_PLUGIN_MAX_VOLUME_BYTES", 1<<30),
	}
}

func (c Config) Validate() error {
	if c.VolumeRoot == "" || c.VolumeRoot[0] != '/' {
		return fmt.Errorf("volume root must be absolute: %q", c.VolumeRoot)
	}
	switch c.Backend {
	case "tongsuo", "aes_fallback":
	default:
		return fmt.Errorf("unknown CDS_SM4_AEAD_BACKEND %q (want tongsuo|aes_fallback)", c.Backend)
	}
	if c.MaxVolumeBytes <= 0 {
		return fmt.Errorf("max volume bytes must be positive")
	}
	return nil
}
