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

_json_field() { # $1=file  $2..=候选键（点号可嵌套：cubesandbox.git_sha）; 打印第一个非空字符串值
  "$PY" - "$1" "${@:2}" <<'PYEOF'
import json, sys
path, keys = sys.argv[1], sys.argv[2:]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
for k in keys:
    cur, ok = d, True
    for part in k.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            ok = False
            break
    if ok and isinstance(cur, str) and len(cur) >= 7:
        print(cur)
        break
PYEOF
}

PIN="${CUBE_PIN:-}"
if [ -z "$PIN" ] && [ -f "$ROOT/scripts/cube-bundle-manifest.json" ]; then
  # 真实 schema 的 git_sha 嵌套在 cubesandbox 下（顶层查找永远取不到 = 死代码）
  PIN="$(_json_field "$ROOT/scripts/cube-bundle-manifest.json" cubesandbox.git_sha git_sha sha commit || true)"
fi
if [ -z "$PIN" ] && [ -f "$STUBS/_VERSION.json" ]; then
  PIN="$(_json_field "$STUBS/_VERSION.json" commit git_sha || true)"
fi
PIN_SOURCE="manifest/_VERSION/fallback"
[ -n "$PIN" ] || { PIN="$PIN_FALLBACK"; PIN_SOURCE="PIN_FALLBACK"; }
# 参数注入面收敛：PIN 会被拼进 git rev-parse/archive/tag，先做 hex 形状校验
case "$PIN" in
  ''|*[!0-9a-fA-F]*) echo "ERROR: pinned sha 不是合法 hex: $PIN" >&2; exit 3 ;;
esac
if [ "${#PIN}" -lt 7 ] || [ "${#PIN}" -gt 40 ]; then
  echo "ERROR: pinned sha 长度必须在 7..40: $PIN" >&2; exit 3
fi
echo "pinned sha: $PIN (source: ${CUBE_PIN:+env override }${PIN_SOURCE})"

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
  # 目录存在不等于仓库有效：否则干净门控被“存在但非仓库”直接绕过
  git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 \
    || { echo "ERROR: $SRC 存在但不是有效 git 仓库（红线 7 干净门控无法核验）" >&2; exit 3; }
  # 命令失败 ≠ “干净”：git 错误（index.lock/dubious ownership/权限）必须硬失败
  if ! dirty="$(git -C "$SRC" status --porcelain 2>&1)"; then
    echo "ERROR: git status 失败，无法核验干净门控（红线 7 fail-closed）: $dirty" >&2
    exit 3
  fi
  if [ -n "$dirty" ]; then
    {
      echo "ERROR: upstream working copy is DIRTY — refusing to run (红线 7: 零 tracked 文件修改):"
      printf '%s\n' "$dirty" | head -20
    } >&2
    exit 3
  fi
fi

# Pin object must exist (read-only verify; a missing object means the repo
# needs a fetch — the only legal upstream write). Resolve to the full oid so a
# 7..40-char short pin never produces a spurious HEAD!=pin warning.
if ! PIN_FULL="$(git -C "$SRC" rev-parse --verify "${PIN}^{commit}" 2>/dev/null)"; then
  echo "ERROR: pinned sha $PIN not present in $SRC — fetch the upstream ref first" >&2
  exit 3
fi
PIN="$PIN_FULL"

HEAD="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
[ -n "$HEAD" ] || { echo "ERROR: 读不到 $SRC 的 HEAD" >&2; exit 3; }
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
[ -d services ] || { echo "ERROR: pkgs/proto/services 在 $PIN 不存在" >&2; exit 3; }
SEARCH=(services)
if [ -d types ]; then
  SEARCH+=(types)
else
  echo "WARNING: pkgs/proto/types 缺失 —— 只生成 services/ 子集，proto_hash 覆盖面将收窄" >&2
fi
mapfile -t PROTO_FILES < <(find "${SEARCH[@]}" -name '*.proto' | LC_ALL=C sort)
[ "${#PROTO_FILES[@]}" -gt 0 ] || { echo "ERROR: no .proto files found under pkgs/proto/{services,types} at $PIN" >&2; exit 3; }
echo "generating ${#PROTO_FILES[@]} protos: ${PROTO_FILES[*]#services/}"

# 生成物集合的单一来源定义（relocate/repath/prune 三处必须严格一致）
find_gen() { find "$@" \( -name '*_pb2*.py' -o -name '*_pb2*.pyi' \) -type f; }

# --- protoc -------------------------------------------------------------------
GEN="$TMP/gen"
rm -rf "$GEN" && mkdir -p "$GEN"
"$PY" -m grpc_tools.protoc \
  -I "$TMP/pkgs/proto" \
  --python_out="$GEN" \
  --grpc_python_out="$GEN" \
  "${PROTO_FILES[@]}"
# 完整性门：逐个 proto 断言双产物，漏生成任何服务都不能“成功”退出
missing=""
for f in "${PROTO_FILES[@]}"; do
  for suffix in _pb2.py _pb2_grpc.py; do
    [ -f "$GEN/$f$suffix" ] || missing="$missing $f$suffix"
  done
done
[ -z "$missing" ] || { echo "ERROR: protoc 未产出:$missing" >&2; exit 3; }

# --- relocate: strip the `services/` prefix so packages sit under app/_stubs/cube/
mkdir -p "$STUBS"
(
  cd "$GEN"
  find_gen . | while IFS= read -r f; do
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
  find_gen . | while IFS= read -r f; do
    # 不用 GNU 原地编辑开关（BSD/macOS sed 的 -i 吞参数），临时文件+mv 两边通用
    sed \
      -e 's/^\(from \)services\./\1app._stubs.cube./' \
      -e 's/^\(import \)services\./\1app._stubs.cube./' \
      -e 's/^\(from \)types\./\1app._stubs.cube.types./' \
      -e 's/^\(import \)types\./\1app._stubs.cube.types./' \
      "$f" > "$f.repath.tmp" && mv -f "$f.repath.tmp" "$f"
  done
)

# --- prune stale generated files from an older proto tree ----------------------
(
  cd "$STUBS"
  find_gen . | sed 's|^\./||' | LC_ALL=C sort > "$TMP/have"
  ( cd "$TMP/pkgs/proto"
    for f in "${PROTO_FILES[@]}"; do
      base="${f%.*}"
      echo "${base#services/}_pb2.py"
      echo "${base#services/}_pb2_grpc.py"
      # grpc_tools 会随 python_out 产 .pyi；want 清单不含它们时 prune 会把
      # 刚生成的类型存根当陈旧文件静默清空（repath 本就处理 .pyi，意图是保留）
      echo "${base#services/}_pb2.pyi"
      echo "${base#services/}_pb2_grpc.pyi"
    done | LC_ALL=C sort > "$TMP/want"
  )
  comm -23 "$TMP/have" "$TMP/want" | while IFS= read -r f; do
    [ -f "$f" ] && rm -f "$f" && echo "pruned stale: $f"
  done
)

# --- package markers (never overwrite an existing __init__.py) -----------------
mkdir -p "$ROOT/app/_stubs"
[ -f "$ROOT/app/_stubs/__init__.py" ] || printf '' > "$ROOT/app/_stubs/__init__.py"
find "$STUBS" -name __pycache__ -prune -o -type d -print | while IFS= read -r d; do
  init="$d/__init__.py"
  if [ ! -f "$init" ]; then
    printf '"""CubeSandbox gRPC stubs (generated — SS-10 CB-00). Do not edit; see _VERSION.json."""\n' > "$init"
  fi
done

# --- _VERSION.json (spec §5.2 fields + tasking fields, superset) ---------------
if ! TAGS_AT_PIN="$(git -C "$SRC" tag --points-at "$PIN" 2>"$TMP/tag_err" | LC_ALL=C sort)"; then
  echo "WARNING: git tag --points-at 失败（$(head -1 "$TMP/tag_err" 2>/dev/null)）—— tag 将记为 unknown" >&2
  TAGS_AT_PIN=""
elif [ -z "$TAGS_AT_PIN" ]; then
  echo "NOTE: 该 pin 上没有 tag（mirror 可能未 fetch refs/tags），tag 将记为 unknown" >&2
fi
TAG="$(printf '%s\n' "$TAGS_AT_PIN" | grep -E '^v[0-9]' | head -1 || true)"
[ -n "$TAG" ] || TAG="$(printf '%s\n' "$TAGS_AT_PIN" | head -1 || true)"
TAG="${TAG:-unknown}"

export CUBE_TAGS_AT_PIN="$TAGS_AT_PIN"
# 先写临时区：import 自检通过后才原子落盘，失败时仓库不会留在“元数据声称已生成”
# 但代码不可导入的损坏态。
"$PY" - "$STUBS" "$TMP/pkgs/proto" "$PIN" "$TAG" "$TMP/_VERSION.json" <<'PYEOF'
import hashlib, json, os, sys, datetime

stubs, proto_root, pin, tag, out_path = sys.argv[1:6]

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
    for dp, dn, fns in os.walk(stubs)
    for fn in fns
    # __pycache__/*.pyc 与解释器/时间戳相关，计入 stub_hash 会让同一 pin 出不同 hash
    if "__pycache__" not in os.path.relpath(os.path.join(dp, fn), stubs).split(os.sep)
    and os.path.relpath(os.path.join(dp, fn), stubs) != "_VERSION.json"
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
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(version, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("staged _VERSION.json (落盘推迟到 import 自检通过后):", json.dumps(version))
PYEOF

# --- self-check: the generated package must import -----------------------------
# 覆盖面 = 全部生成服务（不是只抽 cubebox+volumeplugin 两个）；-B 禁止自检把
# __pycache__ 写进 stubs 目录。
IMPORT_STMTS=""
for f in "${PROTO_FILES[@]}"; do
  mod="${f#services/}"; mod="${mod%.proto}"
  IMPORT_STMTS="${IMPORT_STMTS}from app._stubs.cube.${mod//\//.} import ${mod##*/}_pb2, ${mod##*/}_pb2_grpc\n"
done
(
  cd "$ROOT"
  printf '%b' "$IMPORT_STMTS" > "$TMP/imports.py"
  "$PY" -B -c "
import pathlib, sys
src = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
exec(compile(src, 'generated-imports', 'exec'))
from app._stubs.cube.cubebox.v1 import cubebox_pb2, cubebox_pb2_grpc
assert cubebox_pb2_grpc.CubeboxMgrStub
assert cubebox_pb2.RunCubeSandboxRequest.DESCRIPTOR.name == 'RunCubeSandboxRequest'
print('import self-check ok: all generated stub modules import cleanly')
" "$TMP/imports.py"
)

mv -f "$TMP/_VERSION.json" "$STUBS/_VERSION.json"

echo "gen-cube-stubs.sh: done (pin $PIN, tag $TAG)"
