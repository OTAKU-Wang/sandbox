package main

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"time"
)

// KMSClient talks to the CDS control-plane KMS for volume DEK lifecycle.
//
// Documented contract (SS-10 CB-03 spec step 2/4; control-plane routes live in
// app/api/kms.py).
// The plugin is fail-closed: any transport/HTTP error aborts
// the caller's operation, there is no offline mode.
//
//	Register: POST   {base}/api/v1/kms/volume-keys
//	          body {volume_id, name, dek_hex, cipher}     -> {key_id}
//	Unwrap:   POST   {base}/api/v1/kms/volume-keys/{ref}/distribute
//	          body {session_id}                           -> {key_hex}
//	Revoke:   DELETE {base}/api/v1/kms/volume-keys/{ref} -> 204
//
// Auth: Authorization: Bearer <CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN>.
// mTLS with certificate_service CA is the production transport (spec CB-03
// step 2); the client carries a *http.Client so the transport is injectable.
type KMSClient struct {
	base  string
	token string
	hc    *http.Client
}

const envKMSTimeoutMS = "CDS_VOLUME_PLUGIN_KMS_TIMEOUT_MS"

func kmsTimeout() time.Duration {
	if v, err := strconv.ParseInt(os.Getenv(envKMSTimeoutMS), 10, 64); err == nil && v > 0 {
		return time.Duration(v) * time.Millisecond
	}
	return 30 * time.Second
}

func NewKMSClient(baseURL, token string) *KMSClient {
	return &KMSClient{
		base:  baseURL,
		token: token,
		hc:    &http.Client{Timeout: kmsTimeout()},
	}
}

func (c *KMSClient) do(ctx context.Context, method, path string, body any, out any) error {
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, rd)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return fmt.Errorf("kms %s %s: %w (fail-closed)", method, path, err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 {
		return fmt.Errorf("kms %s %s: HTTP %d: %s (fail-closed)", method, path, resp.StatusCode, string(data))
	}
	if out != nil && len(data) > 0 {
		if err := json.Unmarshal(data, out); err != nil {
			return fmt.Errorf("kms %s %s: bad response: %w", method, path, err)
		}
	}
	return nil
}

// RegisterDEK registers a plugin-generated volume DEK and returns the
// wrapped-key reference (key_id) that goes into private_data. `cipher` is this
// plugin's Cipher.Name(): the control plane records it verbatim so the audit
// record states which cipher actually sealed the volume instead of assuming
// SM4-GCM (the default backend is the AES fallback).
func (c *KMSClient) RegisterDEK(ctx context.Context, volumeID, name, cipher string, dek []byte) (string, error) {
	var resp struct {
		KeyID string `json:"key_id"`
	}
	err := c.do(ctx, http.MethodPost, "/api/v1/kms/volume-keys", map[string]string{
		"volume_id": volumeID,
		"name":      name,
		"dek_hex":   hex.EncodeToString(dek),
		"cipher":    cipher,
	}, &resp)
	if err != nil {
		return "", err
	}
	if resp.KeyID == "" {
		return "", fmt.Errorf("kms register: empty key_id (fail-closed)")
	}
	return resp.KeyID, nil
}

// UnwrapDEK fetches the plaintext DEK for a session (distribute_key
// semantics: session-scoped, TTL — reuse per spec step 4).
func (c *KMSClient) UnwrapDEK(ctx context.Context, ref, sessionID string) ([]byte, error) {
	var resp struct {
		KeyHex string `json:"key_hex"`
	}
	err := c.do(ctx, http.MethodPost, "/api/v1/kms/volume-keys/"+ref+"/distribute",
		map[string]string{"session_id": sessionID}, &resp)
	if err != nil {
		return nil, err
	}
	dek, err := hex.DecodeString(resp.KeyHex)
	if err != nil || len(dek) != dekLen {
		return nil, fmt.Errorf("kms unwrap: bad key_hex (fail-closed)")
	}
	return dek, nil
}

// RevokeDEK destroys the wrapped DEK at the control plane.
func (c *KMSClient) RevokeDEK(ctx context.Context, ref string) error {
	return c.do(ctx, http.MethodDelete, "/api/v1/kms/volume-keys/"+ref, nil, nil)
}
