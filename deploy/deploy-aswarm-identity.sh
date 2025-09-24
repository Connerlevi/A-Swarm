#!/usr/bin/env bash
# Deploy A-SWARM Identity Management (cert-manager path)
# Production-grade, zero-compromise workload identity
set -Eeuo pipefail

# ------------ Config ------------
NAMESPACE="${NAMESPACE:-aswarm}"
CM_NAMESPACE="${CM_NAMESPACE:-cert-manager}"
CM_RELEASE="${CM_RELEASE:-cert-manager}"
CM_VERSION="${CM_VERSION:-v1.13.2}" # override via env when you bump
YES="${YES:-false}" # YES=true to auto-continue prompts
# ---------------------------------

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
fail() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }
warn() { echo -e "${YELLOW}WARN:${NC} $*"; }
info() { echo -e "${GREEN}INFO:${NC} $*"; }

trap 'fail "Command failed (line $LINENO): $BASH_COMMAND"' ERR

echo "=== A-SWARM Identity Deployment (cert-manager) ==="
echo

# 1) Platform detection (FYI for local dev)
echo "1. Detecting Kubernetes platform..."
if kubectl get nodes -o jsonpath='{.items[0].status.nodeInfo.containerRuntimeVersion}' 2>/dev/null | grep -qi docker; then
  if kubectl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null | grep -q '^docker-desktop'; then
    warn "Docker Desktop detected. CSI-based identities (SPIRE path) can be flaky here; cert-manager path is typically OK."
    if [[ "$YES" != "true" ]]; then
      read -p "Continue? (y/N) " -n 1 -r; echo
      [[ $REPLY =~ ^[Yy]$ ]] || fail "Cancelled by user."
    fi
  fi
fi

# 2) Ensure cert-manager (via Helm)
echo "2. Checking cert-manager installation..."
if ! kubectl get crd certificates.cert-manager.io >/dev/null 2>&1; then
  command -v helm >/dev/null || fail "Helm not found. Install Helm: https://helm.sh/docs/intro/install/"
  info "Installing cert-manager (CRDs + controller) into namespace ${CM_NAMESPACE}..."
  helm repo add jetstack https://charts.jetstack.io --force-update
  helm repo update
  kubectl get ns "${CM_NAMESPACE}" >/dev/null 2>&1 || kubectl create namespace "${CM_NAMESPACE}"
  helm upgrade --install "${CM_RELEASE}" jetstack/cert-manager \
    --namespace "${CM_NAMESPACE}" \
    --version "${CM_VERSION}" \
    --set installCRDs=true \
    --set global.leaderElection.namespace="${CM_NAMESPACE}" \
    --wait --timeout 5m || fail "Helm install of cert-manager failed."
else
  info "cert-manager CRDs present; ensuring core deployments are ready..."
fi

# 3) Wait for CRDs & controller pods
echo "3. Waiting for cert-manager to be ready..."
kubectl wait --for=condition=Established --timeout=120s \
  crd/certificates.cert-manager.io \
  crd/certificaterequests.cert-manager.io \
  crd/clusterissuers.cert-manager.io \
  crd/issuers.cert-manager.io || warn "Some CRDs did not report Established; continuing."

kubectl wait --for=condition=Available --timeout=300s -n "${CM_NAMESPACE}" \
  deploy/cert-manager deploy/cert-manager-webhook deploy/cert-manager-cainjector || warn "One or more cert-manager deployments not yet Available."

# 4) Apply A-SWARM identity objects
echo "4. Applying A-SWARM identity manifests..."
kubectl apply -f cert-manager-aswarm-identity.yaml

# 5) RBAC/policy hardening (conditional)
echo "5. Applying RBAC hardening (conditional)..."
APPR_OK=false
if kubectl api-resources | grep -q 'certificaterequestpolicies.policy.cert-manager.io'; then
  APPR_OK=true
elif kubectl get crd certificaterequestpolicies.policy.cert-manager.io >/dev/null 2>&1; then
  APPR_OK=true
fi

GK_OK=false
if kubectl api-resources | grep -q 'constrainttemplates.templates.gatekeeper.sh'; then
  GK_OK=true
fi

if $APPR_OK || $GK_OK; then
  kubectl apply -f cert-manager-rbac-hardening.yaml
else
  warn "Approver-Policy and Gatekeeper CRDs not found; skipping policy enforcement objects."
  warn "Install cert-manager-approver-policy OR Gatekeeper to enable issuance policy enforcement."
fi

# 6) Wait for ClusterIssuer Ready
echo "6. Waiting for ClusterIssuer/aswarm-ca-issuer to be Ready..."
# kubectl wait supports arbitrary condition for many CRDs; fall back to loop if it fails
if ! kubectl wait --for=condition=Ready --timeout=120s clusterissuer/aswarm-ca-issuer 2>/dev/null; then
  # Fallback loop
  for i in {1..60}; do
    st=$(kubectl get clusterissuer aswarm-ca-issuer -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
    [[ "$st" == "True" ]] && break
    sleep 2
  done
  st=$(kubectl get clusterissuer aswarm-ca-issuer -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)
  [[ "$st" == "True" ]] || { kubectl describe clusterissuer aswarm-ca-issuer || true; fail "ClusterIssuer not Ready."; }
fi

# 7) Wait for Certificates
echo "7. Waiting for certificate issuance in namespace ${NAMESPACE}..."
kubectl get ns "${NAMESPACE}" >/dev/null 2>&1 || kubectl create ns "${NAMESPACE}" >/dev/null
CERTS=(pheromone-identity sentinel-identity redswarm-identity blueswarm-identity)
NOT_READY=()
for c in "${CERTS[@]}"; do
  echo -n " - $c ... "
  ok=false
  for i in {1..60}; do
    st=$(kubectl -n "${NAMESPACE}" get certificate "$c" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
    if [[ "$st" == "True" ]]; then ok=true; break; fi
    sleep 2
  done
  if $ok; then echo "Ready"; else echo "NOT READY"; NOT_READY+=("$c"); fi
done

# 8) Optional preflight job (only if template Job exists)
echo "8. Running preflight validation (if job template exists)..."
if kubectl -n "${NAMESPACE}" get job aswarm-identity-preflight >/dev/null 2>&1; then
  kubectl create job "aswarm-preflight-$(date +%s)" --from=job/aswarm-identity-preflight -n "${NAMESPACE}" || warn "Preflight job creation failed."
else
  warn "Preflight job template not found; skipping."
fi

# 9) Summary
echo
echo "=== Deployment Summary ==="
if [[ ${#NOT_READY[@]} -eq 0 ]]; then
  echo -e "${GREEN}✅ A-SWARM identity system deployed successfully!${NC}"
else
  echo -e "${YELLOW}⚠ Some certificates are not Ready:${NC} ${NOT_READY[*]}"
  for c in "${NOT_READY[@]}"; do
    echo "---- describe $c ----"
    kubectl -n "${NAMESPACE}" describe certificate "$c" || true
  done
fi

echo
echo "Workload identities:"
echo " - pheromone: spiffe://aswarm.local/ns/${NAMESPACE}/sa/aswarm-pheromone"
echo " - sentinel:  spiffe://aswarm.local/ns/${NAMESPACE}/sa/aswarm-sentinel"
echo " - redswarm:  spiffe://aswarm.local/ns/${NAMESPACE}/sa/aswarm-redswarm"
echo " - blueswarm: spiffe://aswarm.local/ns/${NAMESPACE}/sa/aswarm-blueswarm"

echo
echo "Mount these secrets in your pods as needed:"
echo " pheromone-tls, sentinel-tls, redswarm-tls, blueswarm-tls"
echo
echo "To test RBAC enforcement:"
echo " kubectl create job --from=job/aswarm-rbac-test rbac-test-\$(date +%s) -n ${NAMESPACE}"
echo
echo "Tip: set YES=true to auto-approve prompts; override versions via CM_VERSION."