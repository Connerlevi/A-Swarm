#!/usr/bin/env bash
# Deploy SPIRE (server, agent, registrar) for A-SWARM Protocol V4

set -euo pipefail

NAMESPACE="${NAMESPACE:-spire-system}"
KUBECTL="${KUBECTL:-kubectl}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVER_YAML="${SERVER_YAML:-$DIR/spire-deployment-fixed.yaml}"
REGISTRAR_YAML="${REGISTRAR_YAML:-$DIR/spire-workload-registrar-fixed.yaml}"
CSI_YAML="${CSI_YAML:-$DIR/spiffe-csi-driver-fixed.yaml}"

echo "=== A-SWARM SPIRE Deployment ==="
echo "Namespace: $NAMESPACE"
echo

# Optional: relax PSS if your cluster enforces it (comment out if not needed)
$KUBECTL get ns "$NAMESPACE" >/dev/null 2>&1 || $KUBECTL create ns "$NAMESPACE"
$KUBECTL label ns "$NAMESPACE" \
  pod-security.kubernetes.io/enforce=privileged \
  pod-security.kubernetes.io/audit=privileged \
  pod-security.kubernetes.io/warn=privileged \
  --overwrite >/dev/null 2>&1 || true

echo "Step 0: Ensuring SPIFFE CSI driver is installed and ready..."
if ! $KUBECTL get ds -A | grep -q spiffe-csi-driver; then
  if [[ -f "$CSI_YAML" ]]; then
    echo " Installing CSI driver from $CSI_YAML"
    $KUBECTL apply -f "$CSI_YAML"
  else
    echo " ERROR: SPIFFE CSI driver not found and CSI_YAML ($CSI_YAML) missing."
    echo " Please install the SPIFFE CSI Driver before continuing."
    exit 1
  fi
fi
# Wait for CSI DaemonSet to be ready (search all namespaces)
CSI_NS="$($KUBECTL get ds -A | awk '/spiffe-csi-driver/{print $1; exit}')"
echo " Waiting for CSI driver readiness in $CSI_NS..."
$KUBECTL -n "$CSI_NS" rollout status ds/spiffe-csi-driver --timeout=300s

echo
echo "Step 1: Deploying SPIRE Server (StatefulSet) and Agent (DaemonSet)..."
$KUBECTL apply -f "$SERVER_YAML"

echo " Waiting for SPIRE Server pods to be Ready..."
$KUBECTL -n "$NAMESPACE" wait --for=condition=ready pod -l app=spire-server --timeout=300s

echo " Waiting for SPIRE Agent DaemonSet to be Ready on all nodes..."
$KUBECTL -n "$NAMESPACE" rollout status ds/spire-agent --timeout=300s

echo
echo "Step 2: Deploying K8s Workload Registrar..."
$KUBECTL apply -f "$REGISTRAR_YAML"
echo " Waiting for Registrar to be Ready..."
$KUBECTL -n "$NAMESPACE" wait --for=condition=ready pod -l app=spire-k8s-workload-registrar --timeout=300s

echo
echo "Step 3: Verification checks..."

echo " SPIRE Server healthcheck..."
if ! $KUBECTL -n "$NAMESPACE" exec deploy/spire-server -- spire-server healthcheck >/dev/null 2>&1; then
  echo " WARN: spire-server healthcheck failed (check logs)."
fi

echo " SPIRE Agent healthcheck (first pod)..."
AGENT_POD="$($KUBECTL -n "$NAMESPACE" get pods -l app=spire-agent -o jsonpath='{.items[0].metadata.name}')"
if ! $KUBECTL -n "$NAMESPACE" exec "$AGENT_POD" -- spire-agent healthcheck >/dev/null 2>&1; then
  echo " WARN: spire-agent healthcheck failed on $AGENT_POD."
fi

echo " Registrar metrics sanity check..."
PF_PORT=18080
$KUBECTL -n "$NAMESPACE" port-forward deploy/spire-k8s-workload-registrar ${PF_PORT}:8080 >/dev/null 2>&1 &
PF_PID=$!
cleanup() { kill $PF_PID >/dev/null 2>&1 || true; }
trap cleanup EXIT
sleep 4
if ! curl -fsS "http://127.0.0.1:${PF_PORT}/metrics" | head -n 5; then
  echo " WARN: Registrar metrics not reachable (check logs)."
fi
cleanup
trap - EXIT

echo
echo "Step 4: Ensuring A-SWARM namespaces & service accounts..."
for ns in aswarm aswarm-test; do
  $KUBECTL create ns "$ns" --dry-run=client -o yaml | $KUBECTL apply -f -
  $KUBECTL label ns "$ns" app.kubernetes.io/part-of=aswarm --overwrite
  for sa in pheromone sentinel api redswarm; do
    $KUBECTL -n "$ns" create serviceaccount "${ns}-${sa}" --dry-run=client -o yaml | $KUBECTL apply -f -
  done
done

echo
echo "Step 5: Agent attestation check (should list attested agents)..."
if ! $KUBECTL -n "$NAMESPACE" exec deploy/spire-server -- spire-server agent list; then
  echo " WARN: Could not list agents. Investigate server logs."
fi

cat <<'NEXT'
=== SPIRE Deployment Complete ===

Next steps:
1) Mount the SPIFFE CSI volume into A-SWARM pods and set:
   - env: SPIFFE_ENDPOINT_SOCKET=unix:///spiffe-workload-api/spire-agent.sock
   - volume:
       csi:
         driver: csi.spiffe.io
   (Already shown in the registrar-fixed.yaml examples.)

2) Verify SPIFFE entries are created automatically:
   kubectl -n spire-system exec deploy/spire-server -- spire-server entry list

3) From any A-SWARM pod with the CSI mount:
   kubectl exec <pod> -- \
     spire-agent api fetch x509 -socketPath /spiffe-workload-api/spire-agent.sock

4) Watch logs:
   kubectl -n spire-system logs deploy/spire-k8s-workload-registrar -f
NEXT