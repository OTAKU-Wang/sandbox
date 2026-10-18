#!/usr/bin/env bash
# =============================================================================
# SS-10 CB-00 · cube-upstream-check.sh — upstream contract drift detector
#
# Spec: specs/ss-10-cube-dataplane-connector.md §4 CB-00 步骤 3/4, §5.3
#
# Compares the upstream repo's current HEAD against the pinned sha and diffs
# ONLY the versioned contract-path set (scripts/cube-contract-paths.txt).
# No network: works entirely on the local upstream clone/working copy.
#
# Env:
#   CUBE_UPSTREAM_DIR  upstream repo (default ~/Documents/workspace/CubeSandbox)
#   CUBE_PIN           pin override (test hook; otherwise manifest > _VERSION.json > constant)
#
# Exit codes:
#   0 = no contract drift (either HEAD == pin, or only non-contract paths changed… see 2)
#       precisely: no contract-path file changed between pin and HEAD
#   1 = CONTRACT DRIFT: contract paths changed — breaking candidates are printed
#       (diff stat + per-path change list); a human must review against spec §二 before any pin push
#   2 = non-contract drift only: upstream moved, but none of the watched
#       contract paths changed (info, non-fatal — auto-follow may proceed)
#   3 = error (repo/pin/python problem)
#
# Note: spec §4 步骤 3 originally reserved exit 2 for "错误"; the CB-00 tasking
# reassigns 2 to "non-contract drift only (info, non-fatal)", so hard errors
# use 3. The CI workflow gates on 1 (fail) and treats 2 as a notice.
# =============================================================================
set -euo pipefail

# mapfile 与下面的数组用法需要 bash>=4；旧系统（macOS /bin/bash 3.2）必须早拒绝
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  echo "ERROR: cube-upstream-check.sh 需要 bash >= 4（当前 ${BASH_VERSION:-unknown}，mapfile 不可用）" >&2
  exit 3
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDIR="${CUBE_UPSTREAM_DIR:-$HOME/Documents/workspace/CubeSandbox}"
PATHS_FILE="$ROOT/scripts/cube-contract-paths.txt"
PIN_FALLBACK="9b75d33907ed7336b1eef73a935ae2d1990d9b35"

die() { echo "ERROR: $*" >&2; exit 3; }

[ -d "$UPDIR" ] || die "upstream dir not found: $UPDIR (set CUBE_UPSTREAM_DIR)"
git -C "$UPDIR" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $UPDIR"
[ -f "$PATHS_FILE" ] || die "contract-path list missing: $PATHS_FILE"

PY="$(command -v python3 || true)"

# --- resolve the pin (same precedence as gen-cube-stubs.sh) -------------------
# 注意：manifest 的真实 schema 把 git_sha 嵌在 cubesandbox 对象下（CB-02 拥有该
# 文件），只查顶层键是永远取不到的死代码 → 静默 fallback 到常量，漂移检测形同虚设。
_json_keys() { # $1=file 其余=候选键（点号=嵌套路径）
  "$PY" -c '
import json,sys
path=sys.argv[1]; keys=sys.argv[2:]
try: d=json.load(open(path, encoding="utf-8"))
except Exception: sys.exit(0)
if not isinstance(d,dict): sys.exit(0)
for k in keys:
    cur, ok = d, True
    for part in k.split("."):
        if isinstance(cur,dict) and part in cur: cur=cur[part]
        else: ok=False; break
    if ok and isinstance(cur,str) and len(cur)>=7: print(cur); break
' "$1" "${@:2}" 2>/dev/null || true
}

PIN="${CUBE_PIN:-}"
PIN_SOURCE="env(CUBE_PIN)"
if [ -z "$PIN" ] && [ -f "$ROOT/scripts/cube-bundle-manifest.json" ]; then
  # manifest 在场就必须能读：解释器缺失时静默降级到常量 = 假平安
  [ -n "$PY" ] || die "python3 not found 但 $ROOT/scripts/cube-bundle-manifest.json 存在 —— 拒绝盲猜 pin"
  PIN="$(_json_keys "$ROOT/scripts/cube-bundle-manifest.json" cubesandbox.git_sha git_sha sha commit)"
  [ -n "$PIN" ] && PIN_SOURCE="cube-bundle-manifest.json"
fi
if [ -z "$PIN" ] && [ -f "$ROOT/app/_stubs/cube/_VERSION.json" ]; then
  [ -n "$PY" ] || die "python3 not found 但 $ROOT/app/_stubs/cube/_VERSION.json 存在 —— 拒绝盲猜 pin"
  PIN="$(_json_keys "$ROOT/app/_stubs/cube/_VERSION.json" commit git_sha)"
  [ -n "$PIN" ] && PIN_SOURCE="app/_stubs/cube/_VERSION.json"
fi
if [ -z "$PIN" ]; then
  echo "WARN: 所有 pin 来源均空，falling back to built-in constant $PIN_FALLBACK —— 若上游已推进，本检查结论不可信" >&2
  PIN="$PIN_FALLBACK"; PIN_SOURCE="PIN_FALLBACK(fallback)"
fi
case "$PIN" in
  ''|*[!0-9a-fA-F]*) die "pinned sha 不是合法 hex: $PIN" ;;
esac

git -C "$UPDIR" rev-parse --verify --quiet "${PIN}^{commit}" >/dev/null \
  || die "pinned sha $PIN not present in $UPDIR (fetch the upstream ref first)"

# HEAD 取不到（空仓/损坏）必须 die，不能拿空串当“有 HEAD”继续比
HEAD="$(git -C "$UPDIR" rev-parse HEAD)" || die "cannot resolve HEAD in $UPDIR"

# --- contract path set ---------------------------------------------------------
# Windows 侧编辑过的清单可能带 CRLF/行尾空格 → 路径永远匹配不上，契表面静默清零
mapfile -t CONTRACT < <(tr -d '\r' < "$PATHS_FILE" | grep -vE '^[[:space:]]*(#|$)' | sed -e 's/[[:space:]]*$//')
[ "${#CONTRACT[@]}" -gt 0 ] || die "no contract paths parsed from $PATHS_FILE"

# --- full-tree diff pin..HEAD (local refs only — no network) -------------------
# git 失败（浅仓无共同历史/对象缺失）绝不能退化成“无变更”：拿 rc 当失败处理。
# quotepath/ext-diff/textconv/renames 固定：非 ASCII 路径与外部 diff 工具会污染行解析。
if ! CHANGED="$(git -C "$UPDIR" -c core.quotepath=false diff --no-ext-diff --no-textconv --no-renames --name-only "$PIN" "$HEAD" 2>&1)"; then
  die "git diff $PIN..$HEAD 失败（浅仓？对象缺失？）: $(printf '%s\n' "$CHANGED" | head -1)"
fi
AHEAD="$(git -C "$UPDIR" rev-list --count "$PIN..$HEAD" 2>/dev/null || echo '?')"
DESCENDANT="no"
if git -C "$UPDIR" merge-base --is-ancestor "$PIN" "$HEAD" 2>/dev/null; then
  DESCENDANT="yes"
fi

CONTRACT_HITS=()
OTHER=()
while IFS= read -r f; do
  [ -z "$f" ] && continue
  hit=0
  for c in "${CONTRACT[@]}"; do
    case "$f" in
      "$c"|"$c"/*) hit=1; break ;;
    esac
  done
  if [ "$hit" = "1" ]; then CONTRACT_HITS+=("$f"); else OTHER+=("$f"); fi
done <<< "$CHANGED"

echo "==================================================================="
echo "CubeSandbox upstream contract drift check (SS-10 CB-00, spec §5.3)"
echo "==================================================================="
echo "upstream repo : $UPDIR"
echo "pin source    : $PIN_SOURCE"
echo "pinned sha    : $PIN"
echo "current HEAD  : $HEAD"
echo "pin is ancestor of HEAD: $DESCENDANT"
if [ "$AHEAD" = "?" ]; then
  echo "commits after pin   : UNKNOWN (pin 不是 HEAD 祖先或历史被重写 —— 本报告的 drift 结论仅在两侧对象均存在时成立)"
elif [ -n "$AHEAD" ]; then
  echo "commits after pin   : $AHEAD"
fi
TAGS_AT_HEAD="$(git -C "$UPDIR" tag --points-at "$HEAD" 2>/dev/null | tr '\n' ' ' || true)"
if [ -n "$TAGS_AT_HEAD" ]; then echo "tags at HEAD      : $TAGS_AT_HEAD"; fi
NEW_TAGS="$(git -C "$UPDIR" tag --sort=creatordate --merged "$HEAD" --no-merged "$PIN" 2>/dev/null | tr '\n' ' ' || true)"
if [ -n "$NEW_TAGS" ]; then echo "new tags since pin: $NEW_TAGS"; fi
echo "changed files pin..HEAD: $(printf '%s\n' "$CHANGED" | grep -c . || true)"

if [ "${#CONTRACT_HITS[@]}" -eq 0 ]; then
  if [ "${#OTHER[@]}" -eq 0 ]; then
    echo "RESULT: no drift — HEAD is at (or tree-identical to) the pinned sha."
    exit 0
  fi
  echo "RESULT: non-contract drift only (${#OTHER[@]} file(s) changed outside the contract set)."
  echo "  auto-follow is safe per spec §5.3 (契约面未变 → 跟进流)."
  exit 2
fi

echo "RESULT: CONTRACT DRIFT — ${#CONTRACT_HITS[@]} contract path(s) changed. Breaking candidates:"
for f in "${CONTRACT_HITS[@]}"; do echo "  $f"; done
echo "---"
git -C "$UPDIR" -c core.quotepath=false diff --no-ext-diff --no-textconv --stat "$PIN" "$HEAD" -- "${CONTRACT_HITS[@]}" \
  || die "git diff --stat 失败"
echo "---"
echo "Action: review against spec §二 契约基线 (逐条核, 回写 spec), regenerate stubs only if"
echo "regression is green; otherwise keep the pin. Do NOT auto-merge (spec §5.3)."
exit 1
