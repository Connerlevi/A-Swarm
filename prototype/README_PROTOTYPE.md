# A-SWARM — Prototype v1 (10–100 node testbed)

This bundle gives you a fast path to a **controlled testbed** for A-SWARM:
- Stand up a small Kubernetes cluster (k3d / Docker Desktop).
- Deploy **Sentinel** (DaemonSet) and **Pheromone** (Deployment) in *observe* mode.
- Inject a **coordinated anomaly** (lateral traffic fanout).
- Apply a **micro-containment** action (NetworkPolicy isolation) and measure MTTR.
- Capture artifacts for **patent documentation** (timestamps, configs, logs).

> Goal: demonstrate *swarm emergence + detection signal + one clean micro-containment*.
> This is laptop-friendly. Scale node count later with cloud/terraform.

---

## Quick Start (Mac/Linux with k3d)

```bash
# 0) Prereqs: Docker, kubectl, k3d (https://k3d.io/)
# 1) Create cluster (3 worker nodes)
./scripts/setup_k3d.sh
# 2) Deploy A-SWARM components
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/policy-configmap.yaml
kubectl apply -f k8s/sentinel-daemonset.yaml
kubectl apply -f k8s/pheromone-deployment.yaml
# 3) Baseline allow-all then install quarantine template (not active yet)
kubectl apply -f k8s/baseline-allow.yaml
# 4) Launch 30 noisy pods (fake services)
kubectl apply -f k8s/noisy-deployment.yaml
# 5) Inject coordinated anomaly (port-scan fanout across noisy pods)
kubectl apply -f k8s/anomaly-job.yaml

# 6) Trigger micro-containment for offending label (Ring-1)
./scripts/micro_contain.sh app=anomaly aswarm

# 7) Measure & report (single run)
python3 scripts/measure_mttr.py --namespace aswarm

# 8) Run multiple times for percentile metrics
python3 scripts/measure_mttr.py --namespace aswarm --repeat 5
```

## Quick Start (Windows PowerShell + Docker Desktop Kubernetes)

```powershell
# 0) Enable Kubernetes in Docker Desktop; ensure kubectl works
kubectl version

# 1) Apply manifests
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/policy-configmap.yaml
kubectl apply -f k8s/sentinel-daemonset.yaml
kubectl apply -f k8s/pheromone-deployment.yaml
kubectl apply -f k8s/baseline-allow.yaml
kubectl apply -f k8s/noisy-deployment.yaml
kubectl apply -f k8s/anomaly-job.yaml

# 2) Micro-containment
powershell -ExecutionPolicy Bypass -File .\scripts\micro_contain.ps1 -Selector "app=anomaly" -Namespace "aswarm"

# 3) Measure (single run)
python .\scripts\measure_mttr.py --namespace aswarm

# 4) Run multiple times for percentile metrics
python .\scripts\measure_mttr.py --namespace aswarm --repeat 5
```

## What this demonstrates (MVP)

1. **Coordinated anomaly signal**: A noisy job fans out to many pods; Pheromone aggregates counts in a sliding window and creates a rich elevation event with witness counts, confidence scores, and scenario context.

2. **Probe-verified micro-containment**: A NetworkPolicy isolates the offender (Ring-1), with actual connectivity probes verifying the containment is effective (not just a sleep timer).

3. **Signed Action Certificates**: Each run generates a cryptographically signed JSON certificate in `ActionCertificates/` with full audit trail including timestamps, policy hashes, and outcome evidence.

4. **TTL auto-revert safety**: A Kubernetes Job automatically removes the quarantine after the configured TTL (default 120s), ensuring bounded actions.

5. **Production-grade metrics**: 
   - Uses monotonic clocks for accurate interval timing
   - Supports batch runs with P50/P95/P99 percentile reporting
   - Captures containment delay and probe attempts

## Key Improvements

- **Real containment verification**: Probes test actual network connectivity blocks
- **Rich elevation context**: Window size, witness pods, confidence scores
- **Audit trail**: Signed certificates with policy hashes and evidence
- **Safety by default**: Auto-revert ensures actions are always bounded
- **Investor-ready metrics**: Percentile-based KPIs, not just single runs

## Files

- `k8s/` — Kubernetes manifests (namespace, RBAC, DaemonSet/Deployment, policies, anomaly job)
- `scripts/` — setup, micro-containment, and measurement helpers
- `patents/` — lab notebook template and claim-family checklist