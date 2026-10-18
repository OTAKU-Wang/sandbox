#!/bin/bash
# S1 acceptance: all services reachable + healthy.
# Every probe must actually pass — S1_CHECK_DONE is printed ONLY when all of
# them did; any failure prints S1_CHECK_FAILED with the offending probes.
# (Previous version printed the sentinel unconditionally: false green.)
set -Eeuo pipefail
trap 'rc=$?; echo "S1_CHECK_FAILED (line $LINENO, rc=$rc)"; exit "$rc"' ERR

BASE="${CDS_BASE_URL:-http://127.0.0.1:8000}"
FRONTEND="${CDS_FRONTEND_URL:-http://127.0.0.1:3000}"
FAILURES=()

probe_http() { # name url expected-code [grep-pattern]
  local name="$1" url="$2" want="$3" pat="${4:-}" body_file code body rc=0
  body_file=$(mktemp)
  code=$(curl -sS --connect-timeout 5 --max-time 20 -o "$body_file" -w '%{http_code}' "$url") || rc=$?
  body=$(head -c 2000 "$body_file" 2>/dev/null || true)
  rm -f "$body_file"
  if [ "$rc" -ne 0 ]; then FAILURES+=("$name: curl rc=$rc ($url)"); return 0; fi
  if [ "$code" != "$want" ]; then FAILURES+=("$name: HTTP $code != $want ($url)"); return 0; fi
  if [ -n "$pat" ] && ! printf '%s' "$body" | grep -qE "$pat"; then
    FAILURES+=("$name: body missing /$pat/ ($url)"); return 0
  fi
  echo "OK $name: HTTP $code"
}

echo "--- /health ---"
probe_http health "$BASE/health" 200
echo "--- /metrics ---"
probe_http metrics "$BASE/metrics" 200 'cds_|process_|http_'
echo "--- frontend ---"
probe_http frontend "$FRONTEND/" 200 '<html|<!DOCTYPE'

echo "--- container states (including exited) ---"
if command -v docker >/dev/null 2>&1; then
  docker ps -a --filter 'name=cds-' --format '  {{.Names}}: {{.Status}}' | sort || true
  bad=$(docker ps -a --filter 'name=cds-' --format '{{.Names}} {{.Status}}' | grep -v ' Up' || true)
  [ -z "$bad" ] || FAILURES+=("containers not Up: $(echo "$bad" | tr '\n' ';')")
  up=$(docker ps --filter 'name=cds-' -q | grep -c . || true)
  [ "$up" -ge 8 ] || FAILURES+=("expected >=8 cds-* containers Up, found $up")
else
  echo "(docker not on PATH — container leg skipped)"
fi

if [ "${#FAILURES[@]}" -gt 0 ]; then
  printf '\n== S1 failures ==\n'
  printf ' - %s\n' "${FAILURES[@]}"
  echo S1_CHECK_FAILED
  exit 1
fi
echo S1_CHECK_DONE
