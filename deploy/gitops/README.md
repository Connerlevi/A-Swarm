# A-SWARM GitOps Deployment

Git-driven, automated deployment for A-SWARM Blue API using Kustomize + Argo CD.

## Prerequisites

> **Required Infrastructure**:
> - ✅ Kubernetes cluster (1.25+) with `kubectl` access
> - ✅ Kustomize ≥4.0 (`kubectl kustomize` or standalone)
> - ✅ Argo CD installed ([installation guide](https://argo-cd.readthedocs.io/en/stable/getting_started/))
> - ✅ Ingress controller (nginx-ingress, traefik, etc.)
> - ✅ cert-manager (for TLS certificates)
> - ✅ Default StorageClass for PVCs
> - ✅ External Secrets Operator (optional, for dynamic secrets)
> 
> **For Private Repos**: Configure Argo CD repository credentials first:
> ```bash
> argocd repo add https://github.com/<org>/A-SWARM.git --username <user> --password <token>
> ```

## Architecture

Replace manual checksum scripts with automated Git-driven workflows:

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Git Commit    │───▶│   Argo CD Sync   │───▶│ Kubernetes API  │
│ (detection-rules│    │  (auto-rollout)  │    │   (deployment)  │
│    .json)       │    │                  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## Directory Structure

```
deploy/gitops/
├── base/                          # Base Kustomization 
│   ├── kustomization.yaml         # Base resources + configMapGenerator
│   ├── blue-api-deployment.yaml   # Blue API Deployment (references ConfigMap)
│   ├── blue-api-service.yaml      # Service definition
│   ├── blue-api-ingress.yaml      # Ingress for external access
│   ├── blue-api-pvc.yaml          # PersistentVolumeClaim
│   ├── blue-api-networkpolicy.yaml# Zero-trust network rules
│   └── namespace.yaml              # Namespace with PSS labels
├── overlays/
│   ├── development/               # Dev environment overlay
│   │   ├── kustomization.yaml
│   │   └── ingress-patch.yaml    # Dev-specific hostname
│   └── production/                # Prod environment overlay
│       ├── kustomization.yaml
│       ├── ingress-patch.yaml    # Prod hostname + TLS
│       └── resources-patch.yaml  # Production resources/replicas
├── detection-rules/               # Git-tracked detection rules
│   └── detection-rules.json      # Rules content (source of truth)
├── argocd/                        # Argo CD application definitions
│   ├── blue-api-dev.yaml          # Dev environment app
│   └── blue-api-prod.yaml         # Prod environment app
├── monitoring/                    # Optional: Prometheus integration
│   ├── servicemonitor.yaml       # If kube-prometheus-stack installed
│   └── prometheusrule.yaml       # Alert rules
├── .pre-commit-config.yaml       # Pre-commit validation hooks
└── Makefile                       # Convenience commands
```

## Key Concepts

### Automatic hash suffixes

Kustomize's `configMapGenerator` creates ConfigMaps with hash suffixes (e.g., `aswarm-detections-7b9c4f8d9`). When detection rules change:
1. New ConfigMap created with new hash
2. Deployment automatically updated to reference new ConfigMap
3. Pods restart to load new rules
4. Old ConfigMap cleaned up after successful rollout

### Base Kustomization

```yaml
# deploy/gitops/base/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: aswarm

resources:
  - namespace.yaml
  - blue-api-pvc.yaml
  - blue-api-deployment.yaml
  - blue-api-service.yaml
  - blue-api-networkpolicy.yaml
  - blue-api-ingress.yaml

configMapGenerator:
  - name: aswarm-detections
    files:
      - detection-rules.json=../detection-rules/detection-rules.json
    options:
      labels:
        app: aswarm-blue-api
        app.kubernetes.io/name: aswarm
        app.kubernetes.io/component: blue-api-rules

generatorOptions:
  disableNameSuffixHash: false  # Keep this false for auto-rollouts
```

**Important**: The Deployment references `name: aswarm-detections` without hash. Kustomize automatically rewrites this to the hashed name.

## Argo CD Application

### Auto-sync Configuration

```yaml
# deploy/gitops/argocd/blue-api-prod.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: aswarm-blue-api-prod
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://github.com/<org>/A-SWARM.git
    path: deploy/gitops/overlays/production
    targetRevision: main
  destination:
    server: https://kubernetes.default.svc
    namespace: aswarm
  syncPolicy:
    automated:
      prune: true        # Remove resources not in Git
      selfHeal: true     # Auto-sync on manual changes
    syncOptions:
      - CreateNamespace=true
      - PruneLast=true   # Delete resources after new ones ready
    retry:
      limit: 5
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m
```

## Environment Overlays

### Development vs Production

Key differences configured via overlays:

| Setting | Development | Production |
|---------|------------|------------|
| **Image** | `latest` or branch tag | SHA256 digest pinned |
| **Replicas** | 1 | 2+ for HA |
| **Resources** | Lower limits | Higher limits |
| **Ingress Host** | `blue-api-dev.local` | `blue-api.aswarm.io` |
| **TLS** | Self-signed | Let's Encrypt |
| **NetworkPolicy** | Relaxed for debugging | Strict zero-trust |
| **StorageClass** | `standard` | `fast-ssd` |

Example production overlay:
```yaml
# deploy/gitops/overlays/production/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: aswarm
bases:
  - ../../base

images:
  - name: aswarm-blue-api
    digest: sha256:6621a49bdca59d4178b0a117c0ced9323c3f761c0a3ad2653aba4239fa7b26d4

replicas:
  - name: aswarm-blue-api
    count: 2

patchesStrategicMerge:
  - ingress-patch.yaml
  - resources-patch.yaml
```

## Usage Workflows

### 1. Update Detection Rules

```bash
# Edit detection rules
vim deploy/gitops/detection-rules/detection-rules.json

# Validate with pre-commit
pre-commit run --files deploy/gitops/detection-rules/detection-rules.json

# Commit and push
git add deploy/gitops/detection-rules/detection-rules.json
git commit -m "feat: Update privilege escalation detection threshold to 0.4"
git push
```

### 2. Validation

```bash
# Check Argo CD sync status
kubectl -n argocd get application aswarm-blue-api-prod
# or
argocd app get aswarm-blue-api-prod

# Wait for deployment
kubectl -n aswarm wait --for=condition=available deploy/aswarm-blue-api --timeout=300s

# Verify via Ingress (no port-forward needed!)
curl -fsS https://blue-api.aswarm.io/ready | jq '.rules_loaded'

# Check metrics if ServiceMonitor deployed
curl -fsS https://blue-api.aswarm.io/metrics | grep aswarm_blue_rules_loaded
```

### 3. Rollback

Two methods available:

```bash
# Method 1: Git revert
git revert HEAD
git push

# Method 2: Argo CD rollback
argocd app rollback aswarm-blue-api-prod <revision>
# or via UI
```

### 4. Promotion (Dev → Prod)

```bash
# Create PR from development to production branch
git checkout production
git merge development
git push origin production

# Or use GitHub/GitLab PR workflow
```

## Quick Commands (Makefile)

```bash
# Apply directly with kubectl (for testing)
make dev      # kubectl apply -k deploy/gitops/overlays/development
make prod     # kubectl apply -k deploy/gitops/overlays/production

# Check status
make status   # kubectl -n aswarm get deploy,svc,ingress,pvc

# Validate manifests
make validate # kubectl kustomize deploy/gitops/overlays/production

# Clean up
make clean    # kubectl delete -k deploy/gitops/overlays/development
```

## Pre-commit Hooks

Install pre-commit hooks to validate changes before commit:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: check-json
        files: ^deploy/gitops/detection-rules/detection-rules\.json$
      - id: check-yaml
        files: ^deploy/gitops/.*\.yaml$
      - id: end-of-file-fixer
      - id: trailing-whitespace
  
  - repo: https://github.com/syntaqx/kustomize-lint
    rev: v1.0.0
    hooks:
      - id: kustomize-lint
        files: ^deploy/gitops/.*kustomization\.yaml$
```

Install hooks:
```bash
pip install pre-commit
pre-commit install
```

## Monitoring Integration

If `kube-prometheus-stack` is installed:

1. Include monitoring resources in overlay:
```yaml
# deploy/gitops/overlays/production/kustomization.yaml
resources:
  - ../../base
  - ../../monitoring  # ServiceMonitor + PrometheusRule
```

2. Remove `prometheus.io/*` annotations from Deployment (ServiceMonitor handles scraping)

3. Ensure ServiceMonitor has correct label selector:
```yaml
# deploy/gitops/monitoring/servicemonitor.yaml
metadata:
  labels:
    release: kube-prometheus-stack  # Match your Prometheus operator selector
```

## Secret Management

### Option 1: External Secrets Operator (Recommended)
```yaml
# Include in base if ESO installed
resources:
  - blue-api-externalsecret.yaml
```

**Note**: ESO CRDs must be installed cluster-wide first or use sync waves:
```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "1"  # ESO resources
    argocd.argoproj.io/sync-wave: "2"  # Application resources
```

### Option 2: Static Secret (Development)
```yaml
# For demo/dev only - use ESO or Sealed Secrets for production
apiVersion: v1
kind: Secret
metadata:
  name: aswarm-blue-api-auth
type: Opaque
stringData:
  token: "change-me-in-prod"
```

## Troubleshooting

### CRD Ordering Issues

If you see `no matches for kind ExternalSecret`:
1. Install CRDs separately: `kubectl apply -f https://...`
2. Or create separate Argo app for CRDs with earlier sync wave
3. Use `sync-wave` annotations to order resources

### Sync Failures

```bash
# Check events
kubectl -n argocd describe application aswarm-blue-api-prod

# Force sync
argocd app sync aswarm-blue-api-prod --force

# Check pod logs
kubectl -n aswarm logs -l app=aswarm-blue-api --tail=50
```

### ConfigMap Not Updating

Ensure `disableNameSuffixHash: false` in kustomization.yaml

## Migration from Manual Scripts

This GitOps approach eliminates:
- ❌ Manual checksum calculation (`scripts/update-detection-rules.sh`)
- ❌ kubectl apply commands  
- ❌ Deployment annotation updates
- ❌ Port-forward verification scripts

And provides:
- ✅ Automatic hash-based rollouts
- ✅ Git-driven audit trail
- ✅ Declarative configuration
- ✅ Built-in rollback via Git
- ✅ Multi-environment promotion