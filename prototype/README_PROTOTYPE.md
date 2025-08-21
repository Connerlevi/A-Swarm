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

# 7) Measure & report
python3 scripts/measure_mttr.py --namespace aswarm
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

# 3) Measure
python .\scripts\measure_mttr.py --namespace aswarm
```

## What this demonstrates (MVP)

1. **Coordinated anomaly signal**: A noisy job fans out to many pods; Pheromone aggregates counts and logs an elevated event.
2. **Micro-containment**: A NetworkPolicy isolates the offender (Ring-1), stopping the fanout (blast radius ↓).
3. **Audit**: A synthetic Action Certificate JSON is emitted with timestamps for MTTD/MTTR.
4. **All guardrails**: No changes to SIS/PLCs; reversible; TTL bounded (policy auto-revert optional).

Note: Sentinel/Pheromone are minimal here—detectors are deterministic stubs. Replace with real logic as you iterate.

## Files

- `k8s/` — Kubernetes manifests (namespace, RBAC, DaemonSet/Deployment, policies, anomaly job)
- `scripts/` — setup, micro-containment, and measurement helpers
- `patents/` — lab notebook template and claim-family checklist