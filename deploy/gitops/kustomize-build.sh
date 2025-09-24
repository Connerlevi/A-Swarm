#!/bin/bash
# Wrapper script for Kustomize builds
# Uses kubectl kustomize if standalone kustomize is not available

set -euo pipefail

if command -v kustomize >/dev/null 2>&1; then
    kustomize build "$@"
else
    kubectl kustomize "$@"
fi