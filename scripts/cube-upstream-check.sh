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
set -uo pipefail

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
PIN="${CUBE_PIN:-}"
if [ -z "$PIN" ] && [ -n "$PY" ] && [ -f "$ROOT/scripts/cube-bundle-manifest.json" ]; then
  PIN="$("$PY" -c '
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: sys.exit(0)
if isinstance(d,dict):
    for k in ("git_sha","sha","commit"):
        v=d.get(k)
        if isinstance(v,str) and len(v)>=7: print(v); break
' "$ROOT/scripts/cube-bundle-manifest.json" 2>/dev/null || true)"
fi
if [ -z "$PIN" ] && [ -n "$PY" ] && [ -f "$ROOT/app/_stubs/cube/_VERSION.json" ]; then
  PIN="$("$PY" -c '
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: sys.exit(0)
if isinstance(d,dict):
    for k in ("commit","git_sha"):
        v=d.get(k)
        if isinstance(v,str) and len(v)>=7: print(v); break
' "$ROOT/app/_stubs/cube/_VERSION.json" 2>/dev/null || true)"
fi
[ -n "$PIN" ] || PIN="$PIN_FALLBACK"

git -C "$UPDIR" rev-parse --verify --quiet "${PIN}^{commit}" >/dev/null \
  || die "pinned sha $PIN not present in $UPDIR (fetch the upstream ref first)"

HEAD="$(git -C "$UPDIR" rev-parse HEAD)"

# --- contract path set ---------------------------------------------------------
mapfile -t CONTRACT < <(grep -vE '^[[:space:]]*(#|$)' "$PATHS_FILE")
[ "${#CONTRACT[@]}" -gt 0 ] || die "no contract paths parsed from $PATHS_FILE"

# --- full-tree diff pin..HEAD (local refs only — no network) -------------------
CHANGED="$(git -C "$UPDIR" diff --name-only "$PIN" "$HEAD")"
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
echo "pinned sha    : $PIN"
echo "current HEAD  : $HEAD"
echo "pin is ancestor of HEAD: $DESCENDANT"
if [ -n "$AHEAD" ]; then
  echo "commits after pin   : ${AHEAD:-0}"
fi
TAGS_AT_HEAD="$(git -C "$UPDIR" tag --points-at "$HEAD" 2>/dev/null | tr '\n' ' ' || true)"
[ -n "$TAGS_AT_HEAD" ] && echo "tags at HEAD      : $TAGS_AT_HEAD"
NEW_TAGS="$(git -C "$UPDIR" tag --sort=creatordate --merged "$HEAD" --no-merged "$PIN" 2>/dev/null | tr '\n' ' ' || true)"
[ -n "$NEW_TAGS" ] && echo "new tags since pin: $NEW_TAGS"
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
git -C "$UPDIR" diff --stat "$PIN" "$HEAD" -- "${CONTRACT_HITS[@]}"
echo "---"
echo "Action: review against spec §二 契约基线 (逐条核, 回写 spec), regenerate stubs only if"
echo "regression is green; otherwise keep the pin. Do NOT auto-merge (spec §5.3)."
exit 1
