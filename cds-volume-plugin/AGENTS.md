# cds-volume-plugin/ — encrypted-volume plugin for CubeSandbox (scored ~16: 25 tracked files, own go.mod/Makefile/Dockerfile, distinct Go+C domain, socket-wired to the backend)

CDS `cds-encrypted` volume plugin (SS-10 CB-03 / SS-14 RT-07): SM4-GCM (Tongsuo) or AES-GCM (disclosed dev fallback) encrypted sandbox volumes whose DEKs live in the backend KMS. Go binary + C EVP helper; served over a unix socket dialed by `app/services/cube_runtime_adapter.py`.

## STRUCTURE
```
cds-volume-plugin/
├── main.go          # CLI surface: serve | selftest | ctl | parse-annotation
├── plugin.go        # core Plugin (406 L hotspot): CreateVolume/PutVolumeData/Attach/Detach/Destroy/GetVolumeInfo/ReadSandboxFile
├── cipher.go kms.go layout.go rootfs.go server.go cli.go config.go selftest.go
├── plugin_test.go   # mockKMS via httptest; fail-closed negative paths (365 L)
├── kms_tls_test.go      # A179 wire-contract spec (TLS material, plaintext gate, redirects)
├── rootfs_safety_test.go# A196 hostile-guest-path spec (symlink/escape/irregular/bound)
├── rootfs_linux.go      # openContainedNoFollow — openat2 containment (Linux only)
├── rootfs_unsupported.go# fail-closed stub off Linux (铁律: 禁降级)
├── gen/             # protoc-gen-go output — DO NOT EDIT, regenerate
├── proto/cdsvolume/v1/  # CDS-owned extension proto (NOT part of upstream CubeSandbox volumeplugin)
├── sm4gcm/          # C EVP helper (sm4_gcm.c/.h) linking vendored Tongsuo libcrypto
├── Makefile Dockerfile README.md
└── bin/ tongsuo-build/  # build artifacts / Tongsuo install prefix — never commit, never edit
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| DEK lifecycle, crypto-erase | `plugin.go` | key zeroize on every path; idempotent-retry semantics |
| AEAD backend choice | `cipher.go` + `config.go` | `CDS_SM4_AEAD_BACKEND`: `tongsuo` (prod MUST) vs `aes_fallback` (dev-only, WARNING at boot, M-01) |
| KMS client paths | `kms.go` | RegisterDEK/UnwrapDEK/RevokeDEK — must match `app/api/kms.py` + `app/schemas/kms.py` in lockstep (README FLAG: routes were spec'd without HTTP paths) |
| gRPC surface | `server.go` | upstream VolumePluginService (Attach/Detach) + VolumeControllerService (Create/Destroy) + CDS CDSVolumeExtension; socket chmod 0600, best-effort chown root:cds |
| rootfs cold-start read | `rootfs.go` | SS-14 RT-07 host-side loop-mount path; guest paths go through `readSandboxTreeFile` (A196) |
| KMS transport/authN | `kms.go` (`validateKMSEndpoint`, `kmsTLSConfig`) | A179 gate: scheme, CA, client keypair, downgrade WARNINGs, `--allow-insecure-kms` parsed in `main.go` `parseServeFlags` |
| Volume on-disk format | `layout.go` | `<VolumeRoot>/<volume_id>/{manifest.json,data.ct,data.nonce(12B),data.tag(16B)}` |

## CONVENTIONS
- Config purely from `CDS_*` env (`CDS_CUBE_VOLUME_PLUGIN_SOCKET`, `CDS_SM4_AEAD_BACKEND`, `CDS_VOLUME_PLUGIN_*`) — repo-wide prefix convention.
- Files 0600, dirs 0700, stage plaintext 0400; all writes via `writeAtomic` (tmp+rename).
- C helper protocol: key on **stdin, never argv/env** (avoids /proc exposure); framing `key(16)||nonce(12)||aad_len(4,BE)||aad||[tag]||body`; any non-zero exit = hard error.
- Go module self-contained (go 1.25, own go.mod) — independent of the Python venv; static-linking preferred (`sm4_gcm-static` links libcrypto.a, no rpath).
- Mixed Chinese/English comments (铁律, 禁伪造) — repo-wide convention.

## ANTI-PATTERNS (THIS PROJECT)
- **Fail-closed / 禁伪造 铁律: decryption failure is an error, never a plaintext fallback** (~45 explicit sites). Boot self-test failure = refuse to serve; self-test also FAILS if tampered ct/tag is accepted.
- Tongsuo `enc` CLI **FORBIDDEN for AEAD** (it rejects AEAD ciphers) — that is why the EVP C helper exists; do not "simplify" back to the CLI.
- Destroy must KMS-revoke first (crypto-erase); revoke failure ⇒ data RETAINED, never delete with a live key. Detach refCount=0 shreds stage plaintext; shred failure = error.
- Attach with empty `private_data` → refuse (fallback path carries no DEK); >1024 B → refuse; manifest volume_id mismatch → refuse.
- `rootfs.go`: host may loop-mount the sandbox rootfs **READ-ONLY only, never a second writer** (double-mount corrupts). Upstream cold-start sandboxes do not wire declared volumes into the guest.
- KMS client has no offline mode; every transport error aborts. Transport is **enforced** (A179): non-`https` refuses to boot unless the host is a loopback IP literal or the operator sets `CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE=1` / `serve --allow-insecure-kms`; CA/client keypair come from `CDS_VOLUME_PLUGIN_KMS_CA_FILE` / `_CLIENT_CERT` / `_CLIENT_KEY`, redirects are never followed, and `TransportMode()` must describe the wire honestly (never claim mTLS without a keypair).
- `rootfs.go` read-back (A196): anything under the guest's overlay upper layer is **guest-written and hostile** — resolve it with `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS|RESOLVE_NO_XDEV)` + `O_NOFOLLOW|O_NONBLOCK`, require `fstat` regular, read from that fd. `filepath.Join(mountPoint, …)` + `os.ReadFile`/`os.Lstat` on a guest path = arbitrary host file read as root, and a FIFO/device node = hung copy-out plus leaked loop mount. Use `readSandboxTreeFile`, never a path re-open.
- Plugin is NOT wired into `.github/workflows/ci.yml` — `make test` is manual; don't assume CI covers it.

## COMMANDS
```bash
# all from cds-volume-plugin/
make local            # sm4_gcm helper + plugin binary -> bin/
make helper | test | docker | clean
go test ./...         # plugin_test.go
make -C sm4gcm PREFIX=<tongsuo-prefix> sm4_gcm sm4_gcm-static   # links ../tongsuo-build/lib64
docker build -f cds-volume-plugin/Dockerfile -t cds-volume-plugin:<ver> .  # context = repo root; needs scripts/prepare_tongsuo.sh first (Tongsuo is a gitlink)
# Python-side integration
python -m pytest -q tests/test_cds_volume_plugin.py tests/test_kms_volume_keys.py
```
