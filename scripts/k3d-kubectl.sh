#!/bin/bash
# k3d kubectl wrapper — runs kubectl inside the k3d server container
# Usage: bash scripts/k3d-kubectl.sh get pods -n cds-system
docker exec k3d-cds-cluster-server-0 kubectl "$@"
