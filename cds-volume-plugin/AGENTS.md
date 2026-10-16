# cds-volume-plugin/ — encrypted-volume plugin for CubeSandbox (scored ~16: 25 tracked files, own go.mod/Makefile/Dockerfile, distinct Go+C domain, socket-wired to the backend)

CDS `cds-encrypted` volume plugin (SS-10 CB-03 / SS-14 RT-07): SM4-GCM (Tongsuo) or AES-GCM (disclosed dev fallback) encrypted sandbox volumes whose DEKs live in the backend KMS. Go binary + C EVP helper; served over a unix socket dialed by `app/services/cube_runtime_adapter.py`.

## STRUCTURE
```
cds-volume-plugin/
├── main.go          # CLI surface: serve | selftest | ctl | parse-annotation
├── plugin.go        # core Plugin (406 L hotspot): CreateVolume/PutVolumeData/Attach/Detach/Destroy/GetVolumeInfo/ReadSandboxFile
├── cipher.go kms.go layout.go rootfs.go server.go cli.go config.go selftest.go
├── plugin_test.go   # mockKMS via httptest; fail-closed negative paths (365 L)
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
| rootfs cold-start read | `rootfs.go` | SS-14 RT-07 host-side loop-mount path |
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
- KMS client has no offline mode; every transport error aborts; prod transport is mTLS (injectable `*http.Client`).
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
