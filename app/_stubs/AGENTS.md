# app/_stubs/

**Score 40 — generated-code boundary.** 33 Python files (16 `*_pb2*.py` protoc artifacts + 17 `__init__.py`, 13 of which carry the generated "Do not edit" docstring) / 2,290 LOC under `cube/` and `cdsvolume/`. Everything here is machine-generated; the directory exists so the Cube dataplane can talk gRPC without a Go toolchain in the Python image. 17 `.py` files import it (5 `app/services/*`, `app/api/sandbox_nodes.py` + `sandbox_sessions.py`, `tests/conftest.py` and 9 cube tests) — all by full path.

## WHY THIS FILE
It is the one place in `app/` where "fix the code" means "re-run the generator". A hand-edit here is doubly bad: `tests/test_cube_upstream_check.py` recomputes the stub tree hash and goes red against `stub_hash`, and even when that is updated, the next regeneration overwrites the edit anyway.

## LAYOUT
| Path | Source |
|------|--------|
| `cube/cubebox/v1`, `cube/images/v1`, `cube/volumeplugin/v1`, `cube/snapshot/v1`, `cube/errorcode/v1`, `cube/types/v1` | upstream CubeSandbox `pkgs/proto/services/**` + `pkgs/proto/types/**`, extracted at the pinned sha and repathed `services.* → app._stubs.cube.*` (imports only; descriptor bytes untouched) |
| `cube/types/v1` | message-only package — note it holds `descriptor_pb2.py` AND a second `images_pb2.py`; `cube/types/v1/images_pb2` is NOT `cube/images/v1/images_pb2`. Grep by file name alone picks the wrong one |
| `cdsvolume/cdsvolume/v1` | CDS-owned proto (the CDS volume-plugin gRPC service), not upstream. The doubled `cdsvolume/cdsvolume/` segment is the proto package path, not a typo. 3 unary RPCs (`PutVolumeData`, `GetVolumeInfo`, `ReadSandboxFile`); sole consumer `app/services/cube_runtime_adapter.py` (lazy in-function imports) |
| `cube/_VERSION.json` | the pin: `commit`, `tag`, `generated_at`, `generator`, `proto_hash`, `stub_hash`, `tags_at_pin` |

## THE PIN
`app/_stubs/cube/_VERSION.json` is the only authority for which upstream revision these stubs represent (currently `9b75d339…` / `go-sdk-v0.7.3`). `cube_runtime_adapter.pinned_cube_version()` reports it as the fleet's expected CDS Cube runtime version; `CDS_CUBE_PINNED_RUNTIME_VERSION` overrides that only as a *fleet declaration* (e.g. the one-click `v0.7.3-rc2` build), never as a stub claim — so a stale `_VERSION.json` is a reported-version lie, not just a code smell.

## REGENERATING
```bash
scripts/gen-cube-stubs.sh          # needs .venv/bin/python WITH grpcio-tools (build-time only, not in requirements.txt)
scripts/cube-upstream-check.sh     # read-only drift check, never regenerates
```
- Generator input resolution: `CUBE_PIN` > `cube-bundle-manifest.json` > `_VERSION.json` > in-script fallback sha.
- It extracts with `git archive <pin>` (read-only) and **refuses** (exit 3) when: no interpreter / no `grpc_tools`, upstream dir missing, pinned sha absent (needs a fetch — the only legal upstream write), the upstream working copy is DIRTY (红线 7: zero tracked-file modification), or protoc emitted nothing.
- `cube-upstream-check.sh` exits `0` no drift · `1` **contract** paths changed (block, review against spec §二 契约基线, do NOT auto-merge) · `2` non-contract drift only (auto-follow safe) · `3` error. The watched set lives in `scripts/cube-contract-paths.txt` — versioned, changes need review.
- Regeneration is a *deliberate* commit: pin change + `_VERSION.json` + regenerated files in one diff, green regression first.

## RUNTIME PINS (why the venv matters)
The gencode checks its runtime at IMPORT time: `ValidateProtobufRuntimeVersion(...)` demands protobuf gencode `7.35.1` and `GRPC_GENERATED_VERSION = '1.84.0'` raises if the installed grpcio is lower. Consequence recorded repo-wide: `~/.local/bin/alembic` runs system protobuf `7.35.0`, so `import app.main` (which reaches `app._stubs`) dies with a `Protobuf VersionError` that looks like an app bug but is an interpreter bug — always use `.venv/bin/python -m alembic`. `requirements.txt` keeps the two exception pins (`grpcio>=1.84.0`, `protobuf>=7.35.1,<8`) for exactly this reason.

## ANTI-PATTERNS (THIS PROJECT)
- Never hand-edit `*_pb2.py` / `*_pb2_grpc.py` (or their generated `__init__.py` docstrings), and never add a helper inside them — put wrappers in `app/services/cube_node_client.py`.
- Never "fix" a red `stub_hash` assertion by recomputing the hash: restore the generator's output, or regenerate and let it rewrite `_VERSION.json`.
- Never bump the pin to "pick up a fix" without `cube-upstream-check.sh` returning non-1; contract drift is reviewed, not swallowed.
- Services must import stubs by full path (`from app._stubs.cube.cubebox.v1 import cubebox_pb2`) — the repath step only rewrites the stubs' own internal imports.
- Do not treat missing enum/message here as "upstream doesn't have it": check whether the pin predates the feature before proposing a pin bump.
- `cdsvolume/` has **no `_VERSION.json` and no `stub_hash`**, so `tests/test_cube_upstream_check.py` cannot see a hand-edit there — the guard is `cube/`-only. Treat every `cdsvolume` change as unreviewed-by-machine and say so.
- `cdsvolume/v1/cdsvolume_pb2_grpc.py` is headed `DO NOT EDIT!` yet carries the only written statement of the volume contract in hand-authored servicer docstrings (spec §2.9 SM4-GCM at-rest; SS-14 RT-07: upstream cold-start guests do NOT wire declared volumes, the plugin loop-mounts `disk/<cubebox_id>/upper/` READ-ONLY and the host never writes there). Regenerating the CDS-owned proto erases that documentation — copy it forward deliberately, do not let a regen silently drop it.
- `__pycache__/*.cpython-314.pyc` sits inside this tree: residue from the local py3.14 venv (prod image is py3.12). Never treat it as source, never count it, never "clean" it into a diff.
