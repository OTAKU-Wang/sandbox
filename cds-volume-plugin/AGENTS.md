# cds-volume-plugin/ — CDS `cds-encrypted` encrypted-volume plugin for CubeSandbox (scored 15/17: 59 tracked files, own go.mod/Makefile/Dockerfile, Go+C module, socket-wired to the backend)

CDS `cds-encrypted` volume plugin (SS-10 CB-03 / SS-14 RT-07): SM4-GCM (Tongsuo) or AES-GCM (disclosed dev fallback) encrypted sandbox volumes whose DEKs live in the backend KMS. Go 1.25 `package main` + C EVP helper on a unix socket dialed by `app/services/cube_runtime_adapter.py`; 12 prod `.go` (3,120 L) against 32 `_test.go` (6,059 L, 140 Test funcs) — the OCR A175–A205c regression round lives here.

## STRUCTURE
```
cds-volume-plugin/         # go.mod module cds-volume-plugin; deps only grpc / protobuf / x/sys
├── main.go cli.go         # CLI: serve | selftest | ctl | parse-annotation; crypto_posture= boot record
├── plugin.go 610 L        # CreateVolume/PutVolumeData/Attach/Detach/Destroy/GetVolumeInfo + stage ledger, shred, zeroize
├── server.go 488 L        # 3 gRPC services, socket-posture gate, ref ledger, per-volume lock table, mapErr
├── kms.go 465 · cipher.go 281 · config.go 253 · layout.go 231 · rootfs.go 234 · selftest.go 92
├── rootfs_linux.go        # openContainedNoFollow — openat2 containment (//go:build linux)
├── rootfs_unsupported.go  # fail-closed stub off Linux (铁律: 禁降级)
├── *_test.go              # per-finding `a<NNN>[_leg]_test.go`; a00_harness_test.go = self-contained harness
├── gen/cdsvolumev1/       # CDS extension stub — protoc output, DO NOT EDIT
├── gen/volumepluginv1/    # upstream CubeSandbox volumeplugin stub (proto not vendored) — DO NOT EDIT
├── proto/cdsvolume/v1/    # cdsvolume.proto (73 L), CDS-owned: NOT part of the upstream protocol
├── sm4gcm/                # C EVP helper sm4_gcm.c/.h + own Makefile (links Tongsuo <prefix>/lib64)
├── Makefile Dockerfile README.md go.mod go.sum
└── bin/ tongsuo-build/    # gitignored artifacts (with sm4gcm/sm4_gcm*) — never commit, never edit
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Volume lifecycle, crypto-erase | `plugin.go` | zeroize on every exit path incl. the idempotency comparison (A202①); Destroy revokes before shredding |
| AEAD backend + honest posture | `cipher.go` `config.go` `main.go` | `CDS_SM4_AEAD_BACKEND`: `tongsuo` (prod MUST) vs `aes_fallback` (dev default, M-01); `CryptoPosture` → `crypto_posture=` boot line is the machine-readable disclosure (A202⑧) |
| KMS wire contract | `kms.go:387-465` | RegisterDEK/UnwrapDEK/RevokeDEK post `map[string]string` bodies — moves in lockstep with `app/schemas/kms.py` + `app/api/kms.py:438/497/533`; README:124 "控制面待实现 — FLAG" is STALE, the routes exist |
| KMS transport/authN | `kms.go` (`validateKMSEndpoint`, `kmsTLSConfig`, `TransportMode`) | A179: https or loopback-literal only, CA/client keypair, redirects never followed, `--allow-insecure-kms` parsed by `main.go parseServeFlags` |
| gRPC surface + socket posture | `server.go` | nodeService(Attach/Detach) + controllerService(Create/Destroy) + extensionService(PutVolumeData/GetVolumeInfo/ReadSandboxFile); `enforceSocketAccess` is fail-closed (A205①), superseding the old best-effort `chown root:cds` |
| Error classification | `server.go mapErr` + `plugin.go` sentinels | `ErrInvalidVolumeID` / `ErrVolumeNotContained` / `sandboxPathError`; the interim `inputRejectionPrefixes` table must reproduce producer wording byte-for-byte (A199b) |
| Hostile `volume_id` → path | `plugin.go:58` + `layout.go paths()` | validated at the RPC boundary AND again at path build; stage tree = `<base>/cds-<volume_id>` |
| rootfs cold-start read | `rootfs.go` `rootfs_linux.go` | read-only loop mount; guest bytes only via `readSandboxTreeFile` (A196), bounded by `maxSandboxFileBytes` |
| Volume on-disk format | `layout.go` | `<VolumeRoot>/<volume_id>/{manifest.json,data.ct,data.nonce(12B),data.tag(16B)}`; `writeAtomic` + 64-shard lock table |

## CONVENTIONS
- Config purely env, 17 `CDS_*` names (15 read in `config.go`, plus `CDS_VOLUME_PLUGIN_KMS_TIMEOUT_MS` in `kms.go` and `CDS_VOLUME_PLUGIN_ROOTFS_VOLUME_ROOT` in `rootfs.go`); `envBool` honours only `1/true/on/yes`; the trust bundle also reads `SSL_CERT_FILE`/`SSL_CERT_DIR`. Socket default `/run/cds-volume-plugin/cds.sock`, volume root `/var/lib/cds/volumes`.
- Timeouts are **milliseconds** (`CDS_VOLUME_PLUGIN_SUBPROC_TIMEOUT_MS=10000`, A176) — never carry that knob as seconds.
- Files 0600, dirs 0700, staged plaintext 0400; every write through `writeAtomic` (tmp+rename+fsync); volume writes serialised over `volumeWriteLockShards = 64`.
- C helper protocol: DEK on **stdin, never argv/env** (no /proc leak); framing `key(16)||nonce(12)||aad_len(4,BE)||aad||[tag]||body`, encrypt→`tag||ct`, exit `0|1|2|3`; any non-zero exit = hard error. `cipher.go dekLen = 16` is pinned from Python (`tests/test_ocr_a121_cube_dek_length_gate.py`) — move both sides.
- Degrades are fields, not prose: `CryptoPosture` JSON and `TransportMode()` exist so a deploy gate reads `gm_compliant`/`degraded` instead of scraping WARNING text (A202⑧).
- Tests: one file per finding, `a<NNN>[_leg]_test.go`, header comment names the defect; `a00_harness_test.go` builds its own plugin + KMS stand-in (tracks never share `plugin_test.go` helpers); `a175_crypto_posture_doc_test.go` / `a204_build_gates_test.go` derive doc↔code expectations at run time by reflection/regex over README, `config.go`, `sm4gcm/Makefile`, `Dockerfile` — so a new env var without a README row fails the gate, and prose itself is never pinned.
- Go module self-contained (go 1.25, own go.mod) — independent of the Python venv; static linking preferred for image baking (`sm4_gcm-static` links `libcrypto.a`, no rpath).
- Mixed Chinese/English comments (铁律, 禁伪造) — repo-wide convention.

## ANTI-PATTERNS (THIS PROJECT)
- **Fail-closed / 禁伪造 铁律: decryption failure is an error, never a plaintext fallback.** `selftest.go RunSelfTest` runs at boot and on the CLI and must reject tampered ct, tampered tag, tampered AAD and a foreign key — any failure means refuse to serve, and failure lines name the broken property only, never key/AAD/plaintext.
- Tongsuo `enc` CLI **FORBIDDEN for AEAD** (it rejects AEAD ciphers) — that is why the EVP C helper exists; do not "simplify" back to the CLI.
- Bare `go test ./...` on a clean checkout **fails by design** (the GM negative paths must not pass with zero national-crypto coverage). Use `make test`, which builds the helper first (A204②); never add an `os.Stat` branch that neither skips nor fails.
- `volume_id` is hostile input: checked at the RPC boundary and again in `layout.go paths()`, must never resolve outside `VolumeRoot`, and bad requests answer `InvalidArgument` — never `FailedPrecondition`/`codes.Internal` (Cubelet retries 500-class forever).
- Cipher inputs are validated (A177): 16-byte key, 12-byte nonce, 16-byte tag — the AES fallback must not accept another nonce size or AES-192/256. Subprocess timeout: never discard the `Kill` error, never wait on `<-done` unbounded (A178).
- Destroy must KMS-revoke first (crypto-erase); revoke failure ⇒ data RETAINED, never delete with a live key. Destroy honours the server-side attach ledger (A200: `DestroyRequest` carries no ref_count). Shred failure = error; `shredFile` must not unlink after a failed zero-fill; Detach at refCount 0 shreds staged plaintext.
- Attach with empty `private_data` → refuse (fallback path carries no DEK); >1024 B → refuse; manifest volume_id mismatch → refuse. Key buffers zeroized on every exit, including idempotency-comparison paths (A202①).
- KMS client has no offline mode; every transport error aborts. Transport is **enforced** (A179): non-`https` refuses to boot unless the host is a loopback IP literal or `CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE=1` / `serve --allow-insecure-kms`; `TransportMode()` must never claim mTLS without a client keypair. KMS refs are validated as bare path segments before URL concat (A181), upstream error bodies are never spliced into log strings (A182 — the register echo can carry the DEK), and only the expected status counts as success with `io.ReadAll` errors checked (A180).
- `rootfs.go`: loop-mount **READ-ONLY only, never a second writer**; glob metacharacters in the id are rejected. Anything under the guest overlay upper layer is hostile (A196): resolve with `openat2(RESOLVE_BENEATH|NO_SYMLINKS|NO_MAGICLINKS|NO_XDEV)` + `O_NOFOLLOW|O_NONBLOCK`, require `fstat` regular, read from that fd. `filepath.Join(mountPoint, …)` + `os.ReadFile`/`os.Lstat` = arbitrary host read as root; a FIFO/device node = hung copy-out + leaked loop mount. Use `readSandboxTreeFile`, never a path re-open.
- Dockerfile `FROM` lines stay digest-pinned and only the pin **shape** is gated, never a value (A205c: an upstream tag re-push must not redden the gate); the runtime base must actually carry a CA store (A204①: `debian:bookworm-slim` has none → every https KMS call dies on unknown authority).
- `gen/`, `tongsuo-build/`, `bin/`, `sm4gcm/sm4_gcm*` are never hand-edited or committed; Tongsuo is built out-of-tree with no source edits.
- Plugin is NOT wired into `.github/workflows/ci.yml` — `make test` is the only automated door; don't assume a proto change is CI-covered.

## COMMANDS
```bash
# all from cds-volume-plugin/
make local            # helper + plugin binary -> bin/
make test             # builds the helper FIRST, then go test ./...
make helper | docker | clean
go test -run 'TestA199|TestA200' ./...   # per-finding legs; needs the helper (run `make helper` first)
make -C sm4gcm PREFIX=<tongsuo-prefix> sm4_gcm sm4_gcm-static   # links <prefix>/lib64; static = image baking
# Tongsuo prerequisite (no source edits): cd ../Tongsuo && ./Configure --prefix=$PWD/cds-volume-plugin/tongsuo-build no-tests && make -j$(nproc) && make install_sw
docker build -f cds-volume-plugin/Dockerfile -t cds-volume-plugin:<ver> .  # context = repo root; scripts/prepare_tongsuo.sh first (Tongsuo is a gitlink)
# proto regen: NO protoc target exists. proto/cdsvolume/v1/cdsvolume.proto -> gen/cdsvolumev1;
# gen/volumepluginv1 comes from the read-only upstream CubeSandbox. Pin protoc 5.27.3 / protoc-gen-go 1.36.6 / protoc-gen-go-grpc 1.5.1
# Python-side doors (authoritative numbers still come from the remote py3.12 lane):
python -m pytest -q tests/test_cds_volume_plugin.py tests/test_kms_volume_keys.py tests/test_ocr_a121_cube_dek_length_gate.py
```
