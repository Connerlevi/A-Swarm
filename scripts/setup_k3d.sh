#!/usr/bin/env bash
set -euo pipefail

# Create k3d cluster with 3 agent nodes
k3d cluster create aswarm --agents 3 --api-port 6550 -p "8081:80@loadbalancer"

# Verify cluster is ready
kubectl cluster-info
echo "k3d cluster 'aswarm' ready."