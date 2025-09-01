#!/bin/bash
# Deploy A-SWARM with UDP Fast-Path for <200ms MTTD

set -euo pipefail

NAMESPACE="${NAMESPACE:-aswarm}"
FASTPATH_KEY="${ASWARM_FASTPATH_KEY:-$(openssl rand -hex 32)}"

echo "=== A-SWARM Fast-Path Deployment ==="
echo "Namespace: $NAMESPACE"
echo ""

# Create namespace if not exists
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

# Create fast-path secret
echo "Creating fast-path secret..."
kubectl create secret generic aswarm-fastpath-key \
  --namespace=$NAMESPACE \
  --from-literal=key="$FASTPATH_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

# Apply RBAC (assuming it exists)
echo "Applying RBAC..."
kubectl apply -f k8s/rbac.yaml -n $NAMESPACE || true

# Deploy Pheromone with UDP listener
echo "Deploying Pheromone (dual-path)..."
kubectl apply -f k8s/pheromone-fast-deployment.yaml

# Wait for Pheromone to be ready
echo "Waiting for Pheromone..."
kubectl rollout status deployment/aswarm-pheromone-fast -n $NAMESPACE --timeout=60s

# Get Pheromone service IP
PHEROMONE_IP=$(kubectl get svc aswarm-pheromone-fast -n $NAMESPACE -o jsonpath='{.spec.clusterIP}')
echo "Pheromone service IP: $PHEROMONE_IP"

# Deploy Sentinel with UDP sender
echo "Deploying Sentinel (dual-path)..."
kubectl apply -f k8s/sentinel-fast-daemonset.yaml

# Wait for Sentinels
echo "Waiting for Sentinels..."
kubectl rollout status daemonset/aswarm-sentinel-fast -n $NAMESPACE --timeout=60s

# Show status
echo ""
echo "=== Deployment Status ==="
kubectl get pods -n $NAMESPACE -l 'app.kubernetes.io/name in (sentinel,pheromone)'
echo ""
kubectl get svc -n $NAMESPACE

echo ""
echo "=== Testing Fast-Path ==="
echo "Run this to test UDP fast-path latency:"
echo ""
echo "export ASWARM_FASTPATH_KEY='$FASTPATH_KEY'"
echo "python scripts/test_fast_path.py --mode remote --host $PHEROMONE_IP --packets 100"
echo ""
echo "Or run the integrated test:"
echo "kubectl exec -n $NAMESPACE deployment/aswarm-pheromone-fast -- python scripts/test_fast_path.py --mode loopback"

# Optional: Show logs
if [[ "${SHOW_LOGS:-false}" == "true" ]]; then
    echo ""
    echo "=== Pheromone Logs ==="
    kubectl logs -n $NAMESPACE deployment/aswarm-pheromone-fast --tail=20
    
    echo ""
    echo "=== Sentinel Logs (first node) ==="
    kubectl logs -n $NAMESPACE daemonset/aswarm-sentinel-fast --tail=20 | head -20
fi

echo ""
echo "=== Fast-Path Enabled! ==="
echo "Expected P95 MTTD: <200ms via UDP bypass"
echo "Lease path continues for reliability and audit"