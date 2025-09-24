#!/bin/bash
# Create ConfigMaps with Python code for A-SWARM components

set -euo pipefail

NAMESPACE="${NAMESPACE:-aswarm}"

echo "Creating ConfigMaps with Python code in namespace: $NAMESPACE"

# Create Pheromone code ConfigMap
echo "Creating aswarm-pheromone-code..."
kubectl create configmap aswarm-pheromone-code \
  --namespace=$NAMESPACE \
  --from-file=../pheromone/gossip_v2.py \
  --from-file=../pheromone/signal_types.py \
  --from-file=udp_listener.py=../pheromone/udp_listener.py \
  --from-file=__init__.py=/dev/null \
  --dry-run=client -o yaml | kubectl apply -f -

# Create Sentinel code ConfigMap  
echo "Creating aswarm-sentinel-code..."
kubectl create configmap aswarm-sentinel-code \
  --namespace=$NAMESPACE \
  --from-file=../sentinel/telemetry_v2.py \
  --from-file=fast_path.py=../sentinel/fast_path.py \
  --from-file=__init__.py=/dev/null \
  --dry-run=client -o yaml | kubectl apply -f -

echo "ConfigMaps created successfully!"