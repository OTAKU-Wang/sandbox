#!/usr/bin/env bash
# =============================================================================
# SS-10 CB-00 · gen-cube-stubs.sh — regenerate CubeSandbox gRPC Python stubs
# from the pinned upstream sha.
#
# Spec: specs/ss-10-cube-dataplane-connector.md §4 CB-00 步骤 2, §5.1, §5.2
#
# Flow:
#   1. Resolve the pinned sha, in priority order:
#        CUBE_PIN env override (test hook)
#        > scripts/cube-bundle-manifest.json   (CB-02 owns this file)
#        > app/_stubs/cube/_VERSION.json       (the pin this tree was built from)
#        > built-in constant below (SS-10 v1.1 baseline)
#   2. Extract protos with `git archive <pin> pkgs/proto` — strictly READ-ONLY
#      against the upstream repo, independent of the working-copy's state
#      (spec §5.2: 不依赖工作副本状态、零 checkout 切换), and it never writes to
#      the upstream repo (红线 7).
#   3. grpc_tools.protoc → app/_stubs/cube/
#   4. Repath generated imports: `services.x.y` → `app._stubs.cube.x.y`
#   5. Rewrite _VERSION.json (sha + tag + proto/stub hashes) and import-check.
#
# Import repathing (documented per tasking): the upstream protos are rooted at
# `pkgs/proto` and cross-import as `services/...`. We ship the stubs under
# `app/_stubs/cube/` with the `services/` prefix stripped, so that
#     from app._stubs.cube.cubebox.v1 import cubebox_pb2, cubebox_pb2_grpc
# resolves. The generated `_pb2_grpc.py` modules import their sibling `_pb2`
# module by proto-relative path (`from services.cubebox.v1 import ...`), so a
# small sed repath step is required — the standard technique for this layout
# (grpcio "import rewriting"; see the grpcio-tools FAQ). Only the import lines
# are touched; descriptor bytes are data, not code, and are left as-is.
#
# Refusals (exit 3):
#   * upstream working copy is dirty (git status --porcelain non-empty — 红线 7,
#     spec §6.3 步骤 8 hard gate)
#   * pinned sha object not present in the upstream repo (needs fetch /
#     `reset --hard <upstream ref>` — the only legal upstream writes)
#   * no python interpreter with grpc_tools available
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STUBS="$ROOT/app/_stubs/cube"

# --- python interpreter (needs grpcio-tools, build-time only) ----------------
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "ERROR: no python interpreter (.venv/bin/python or python3) found" >&2; exit 3; }
if ! "$PY" -c 'import grpc_tools' >/dev/null 2>&1; then
  echo "ERROR: $PY lacks grpc_tools (grpcio-tools is a build-time dep, not in requirements.txt)" >&2
  exit 3
fi

# --- pinned sha ---------------------------------------------------------------
# Fallback pin: SS-10 v1.1 contract baseline (2026-10-12 源码复核). Prefer
# scripts/cube-bundle-manifest.json (written by CB-02) when present; key names
# are probed (git_sha / sha / commit) since that file's schema is owned by
# another task.
PIN_FALLBACK="9b75d33907ed7336b1eef73a935ae2d1990d9b35"

_json_field() { # $1=file  $2..=candidate keys; prints first non-empty value
  "$PY" - "$1" "$2" <<'PYEOF'
import json, sys
path, keys = sys.argv[1], sys.argv[2:]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
for k in keys:
    v = d.get(k)
    if isinstance(v, str) and len(v) >= 7:
        print(v)
        break
PYEOF
}

PIN="${CUBE_PIN:-}"
if [ -z "$PIN" ] && [ -f "$ROOT/scripts/cube-bundle-manifest.json" ]; then
  PIN="$(_json_field "$ROOT/scripts/cube-bundle-manifest.json" git_sha sha commit || true)"
fi
if [ -z "$PIN" ] && [ -f "$STUBS/_VERSION.json" ]; then
  PIN="$(_json_field "$STUBS/_VERSION.json" commit git_sha || true)"
fi
[ -n "$PIN" ] || PIN="$PIN_FALLBACK"
echo "pinned sha: $PIN"

# --- upstream source (mirror preferred per spec §5.1, working-copy fallback) --
MIRROR="${CUBE_MIRROR:-/opt/cds/cube-mirror.git}"
UPDIR="${CUBE_UPSTREAM_DIR:-$HOME/Documents/workspace/CubeSandbox}"
if [ -d "$MIRROR" ] && git -C "$MIRROR" rev-parse --git-dir >/dev/null 2>&1; then
  SRC="$MIRROR"; SRC_KIND="mirror"
else
  SRC="$UPDIR"; SRC_KIND="work-copy"
fi
[ -d "$SRC" ] || { echo "ERROR: upstream repo not found: $SRC (set CUBE_UPSTREAM_DIR or CUBE_MIRROR)" >&2; exit 3; }
echo "upstream source: $SRC ($SRC_KIND)"

# 红线 7: the clean working copy must stay zero-diff. A bare mirror has no
# worktree, so the gate applies to work-copies only.
if [ "$SRC_KIND" = "work-copy" ]; then
  dirty="$(git -C "$SRC" status --porcelain 2>/dev/null || true)"
  if [ -n "$dirty" ]; then
    {
      echo "ERROR: upstream working copy is DIRTY — refusing to run (红线 7: 零 tracked 文件修改):"
      printf '%s\n' "$dirty" | head -20
    } >&2
    exit 3
  fi
fi

# Pin object must exist (read-only verify; a missing object means the repo
# needs a fetch — the only legal upstream write).
if ! git -C "$SRC" rev-parse --verify --quiet "${PIN}^{commit}" >/dev/null; then
  echo "ERROR: pinned sha $PIN not present in $SRC — fetch the upstream ref first" >&2
  exit 3
fi

# HEAD == pin is the expected state but NOT a hard gate: spec §5.2 — all proto
# extraction goes through `git archive <sha>`, so generation is independent of
# which ref the working copy happens to be at. Mismatch is disclosed, not hidden.
HEAD="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
if [ "$HEAD" != "$PIN" ]; then
  echo "WARNING: upstream HEAD ($HEAD) != pinned sha ($PIN) — generating from the pin via git archive (spec §5.2); consider \`git -C $SRC reset --hard $PIN\` (红线 7: the only legal upstream write)" >&2
fi

# --- extract protos at the pin (read-only) ------------------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if ! git -C "$SRC" archive "$PIN" pkgs/proto | tar -x -C "$TMP"; then
  echo "ERROR: \`git archive $PIN pkgs/proto\` failed" >&2
  exit 3
fi

# Full import closure of the service surface: every .proto under services/ and
# types/ (spec CB-01 step 1 lists services/{cubebox,volumeplugin,images,
# errorcode,types,...}). third_party/google WKTs are resolved by protoc's
# built-ins, never generated.
cd "$TMP/pkgs/proto"
mapfile -t PROTO_FILES < <(find services types -name '*.proto' 2>/dev/null | LC_ALL=C sort)
[ "${#PROTO_FILES[@]}" -gt 0 ] || { echo "ERROR: no .proto files found under pkgs/proto/{services,types} at $PIN" >&2; exit 3; }
echo "generating ${#PROTO_FILES[@]} protos: ${PROTO_FILES[*]#services/}"

# --- protoc -------------------------------------------------------------------
GEN="$TMP/gen"
rm -rf "$GEN" && mkdir -p "$GEN"
"$PY" -m grpc_tools.protoc \
  -I "$TMP/pkgs/proto" \
  --python_out="$GEN" \
  --grpc_python_out="$GEN" \
  "${PROTO_FILES[@]}"
[ -f "$GEN/services/cubebox/v1/cubebox_pb2.py" ] || { echo "ERROR: protoc did not emit services/cubebox/v1/cubebox_pb2.py" >&2; exit 3; }

# --- relocate: strip the `services/` prefix so packages sit under app/_stubs/cube/
mkdir -p "$STUBS"
(
  cd "$GEN"
  find . \( -name '*_pb2*.py' -o -name '*_pb2*.pyi' \) -type f | while IFS= read -r f; do
    rel="${f#./}"
    rel="${rel#services/}"
    dest="$STUBS/$rel"
    mkdir -p "$(dirname "$dest")"
    mv "$f" "$dest"
  done
)

# --- repath generated imports: services.x.y / types.x.y → app._stubs.cube.* ---
# (see header). Only import lines at column 0 of generated files are touched.
(
  cd "$STUBS"
  find . \( -name '*_pb2*.py' -o -name '*_pb2*.pyi' \) -type f | while IFS= read -r f; do
    sed -i \
      -e 's/^\(from \)services\./\1app._stubs.cube./' \
      -e 's/^\(import \)services\./\1app._stubs.cube./' \
      -e 's/^\(from \)types\./\1app._stubs.cube.types./' \
      -e 's/^\(import \)types\./\1app._stubs.cube.types./' \
      "$f"
  done
)

# --- prune stale generated files from an older proto tree ----------------------
(
  cd "$STUBS"
  find . \( -name '*_pb2*.py' -o -name '*_pb2*.pyi' \) -type f | sed 's|^\./||' | LC_ALL=C sort > "$TMP/have"
  ( cd "$TMP/pkgs/proto"
    for f in "${PROTO_FILES[@]}"; do
      base="${f%.*}"
      echo "${base#services/}_pb2.py"
      echo "${base#services/}_pb2_grpc.py"
    done | LC_ALL=C sort > "$TMP/want"
  )
  comm -23 "$TMP/have" "$TMP/want" | while IFS= read -r f; do
    [ -f "$f" ] && rm -f "$f" && echo "pruned stale: $f"
  done
)

# --- package markers (never overwrite an existing __init__.py) -----------------
mkdir -p "$ROOT/app/_stubs"
[ -f "$ROOT/app/_stubs/__init__.py" ] || printf '' > "$ROOT/app/_stubs/__init__.py"
find "$STUBS" -type d | while IFS= read -r d; do
  init="$d/__init__.py"
  if [ ! -f "$init" ]; then
    printf '"""CubeSandbox gRPC stubs (generated — SS-10 CB-00). Do not edit; see _VERSION.json."""\n' > "$init"
  fi
done

# --- _VERSION.json (spec §5.2 fields + tasking fields, superset) ---------------
TAGS_AT_PIN="$(git -C "$SRC" tag --points-at "$PIN" 2>/dev/null | LC_ALL=C sort || true)"
TAG="$(printf '%s\n' "$TAGS_AT_PIN" | grep -E '^v[0-9]' | head -1 || true)"
[ -n "$TAG" ] || TAG="$(printf '%s\n' "$TAGS_AT_PIN" | head -1 || true)"
TAG="${TAG:-unknown}"

export CUBE_TAGS_AT_PIN="$TAGS_AT_PIN"
"$PY" - "$STUBS" "$TMP/pkgs/proto" "$PIN" "$TAG" <<'PYEOF'
import hashlib, json, os, sys, datetime

stubs, proto_root, pin, tag = sys.argv[1:5]

def h(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()

def rels(root, prefix):
    out = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), root)
            if fn.endswith(".proto") and rel.split(os.sep)[0] in prefix:
                out.append(rel)
    return sorted(out)

buf = b""
for rel in rels(proto_root, {"services", "types"}):
    with open(os.path.join(proto_root, rel), "rb") as f:
        buf += rel.encode() + b"\0" + f.read() + b"\0"
proto_hash = h(buf)

buf = b""
for rel in sorted(
    os.path.relpath(os.path.join(dp, fn), stubs)
    for dp, _, fns in os.walk(stubs)
    for fn in fns
    if os.path.relpath(os.path.join(dp, fn), stubs) != "_VERSION.json"
):
    with open(os.path.join(stubs, rel), "rb") as f:
        buf += rel.encode() + b"\0" + hashlib.sha256(f.read()).hexdigest().encode() + b"\0"

version = {
    # tasking fields
    "commit": pin,
    "tag": tag,
    "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "generator": "gen-cube-stubs.sh",
    # spec §5.2 fields
    "git_sha": pin,
    "cube_version": tag,
    "tags_at_pin": sorted(set(p.strip() for p in os.environ.get("CUBE_TAGS_AT_PIN", "").splitlines() if p.strip())),
    "proto_hash": proto_hash,
    "stub_hash": h(buf),
}
with open(os.path.join(stubs, "_VERSION.json"), "w", encoding="utf-8") as f:
    json.dump(version, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("wrote _VERSION.json:", json.dumps(version))
PYEOF

# --- self-check: the generated package must import -----------------------------
(
  cd "$ROOT"
  "$PY" - <<'PYEOF'
from app._stubs.cube.cubebox.v1 import cubebox_pb2, cubebox_pb2_grpc
from app._stubs.cube.volumeplugin.v1 import volumeplugin_pb2, volumeplugin_pb2_grpc
assert cubebox_pb2_grpc.CubeboxMgrStub
assert cubebox_pb2.RunCubeSandboxRequest.DESCRIPTOR.name == "RunCubeSandboxRequest"
print("import check OK: cubebox + volumeplugin stubs import cleanly")
PYEOF
)

echo "gen-cube-stubs.sh: done (pin $PIN, tag $TAG)"
