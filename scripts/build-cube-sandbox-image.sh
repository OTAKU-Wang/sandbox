#!/usr/bin/env bash
# =============================================================================
# SS-10 CB-04 步骤 4 — build-cube-sandbox-image.sh
#
# Build the CDS L2' scenario image with the CubeEgress root CA baked in.
# The CA artifact is produced by CB-02 node bring-up (node path
# /etc/cube/ca/cube-root-ca.crt — upstream CubeEgress/nginx.conf
# ca_cert_path, verified 2026-10-13) and is copied to the build host by CDS
# ops; this script takes that copy as its argument (or CDS_CUBE_EGRESS_CA).
#
# Usage:
#   scripts/build-cube-sandbox-image.sh <ca-artifact-path> [image-tag]
#
# Honesty (项目铁律): a missing CA is a hard build failure — the image
# would MITM-fail every HTTPS egress, so a CA-less build is never produced
# silently. The same check is the CB-05 template-probe hook:
#   app.services.cube_egress.check_cube_egress_ca_baked(ca_path)
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

CA="${CDS_CUBE_EGRESS_CA:-${1:-}}"
TAG="${2:-cds/cube-sandbox:latest}"

if [ -z "$CA" ]; then
  echo "ERROR: CubeEgress root CA artifact path required." >&2
  echo "  usage: $0 <ca-artifact-path> [image-tag]" >&2
  echo "  source: CB-02 node bring-up (node /etc/cube/ca/cube-root-ca.crt)" >&2
  exit 3
fi
if [ ! -f "$CA" ] || [ ! -s "$CA" ]; then
  echo "ERROR: CubeEgress root CA not found or empty at ${CA} — refusing to" >&2
  echo "build a scenario image without the CA (honest failure; a CA-less" >&2
  echo "image would fail every HTTPS egress at the CubeEgress MITM)." >&2
  exit 3
fi

# Pre-flight probe — the SAME check CB-05's template build invokes.
if ! INFO="$(cd "$ROOT" && "$PY" - "$CA" <<'PYEOF'
import sys
from app.services.cube_egress import check_cube_egress_ca_baked
probe = check_cube_egress_ca_baked(sys.argv[1])
print(f"CA probe ok: {probe['ca_path']} sha256={probe['sha256']} size={probe['size_bytes']}")
PYEOF
)"; then
  echo "ERROR: CA probe failed: $INFO" >&2
  exit 3
fi
printf '%s\n' "$INFO"

# docker build needs the CA inside the context — stage it in a temp dir with
# a fixed name (matches the Dockerfile build-arg default).
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
cp "$ROOT/Dockerfile.cube-sandbox" "$CTX/Dockerfile"
cp "$CA" "$CTX/cube-egress-ca.crt"

echo "building ${TAG} (context: $CTX)"
docker build -t "$TAG" "$CTX"
echo "done: ${TAG}"
