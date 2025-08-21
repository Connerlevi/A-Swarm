#!/usr/bin/env bash
# Usage: ./micro_contain.sh app=anomaly aswarm
set -euo pipefail

selector=${1:-app=anomaly}
ns=${2:-aswarm}

echo "[A-SWARM] Applying quarantine label and NetworkPolicy..."
kubectl -n "$ns" label pods -l "$selector" aswarm/quarantine=true --overwrite
kubectl apply -f k8s/quarantine-template.yaml
echo "[A-SWARM] Quarantine applied."