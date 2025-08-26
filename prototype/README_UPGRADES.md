# A-SWARM Upgrade Pack v2

This upgrade pack adds investor-ready features to the A-SWARM prototype:

## 🎯 What's New

### 📊 KPI Dashboard (`dashboard/`)
- **Streamlit app** showing live MTTD/MTTR metrics with P50/P95/P99 percentiles  
- **Target compliance** tracking (200ms MTTD, 5s MTTR goals)
- **Certificate viewer** with signature verification
- **Export capabilities** (CSV download)

### 📦 Helm Chart (`helm/aswarm-prototype/`)
- **One-command deployment**: `helm install aswarm ./helm/aswarm-prototype`
- **Parameterized values**: namespace, replicas, TTL, selectors
- **Production-ready**: proper templating and resource management

### 🔄 CI/CD Pipeline (`.github/workflows/prototype-e2e.yml`)
- **Automated testing** with kind cluster
- **End-to-end drill** execution
- **Artifact upload** of Action Certificates
- **Trigger on push/PR**

### ✅ Preflight Checks (`scripts/preflight.py`)
- **Environment validation**: kubectl context, NetworkPolicy support
- **Dependency checks**: helm, python availability
- **Dry-run testing**: manifest validation

### 🛠️ Developer Experience (`Makefile`)
- **Quick targets**: `make helm-install`, `make drill`, `make dashboard`
- **Batch operations**: `make drill-repeat` for percentiles
- **Cleanup**: `make clean`, `make helm-uninstall`

## 🚀 Quick Start

### 1. Run the Dashboard
```bash
cd prototype
pip install -r dashboard/requirements.txt
make dashboard
```

### 2. Deploy via Helm
```bash
cd prototype
make preflight          # Check prerequisites
make helm-install       # Deploy everything
make drill             # Run attack simulation
```

### 3. View Results
- Open the dashboard at `http://localhost:8501`
- Check `ActionCertificates/` for signed audit trails
- Run `make report` for CLI summary

## 📈 Investor Demo Flow

1. **Show the dashboard** with target lines and compliance %
2. **Run multiple drills**: `make drill-repeat` 
3. **Open Action Certificate** showing full audit trail
4. **Highlight safety**: TTL auto-revert, probe-verified containment
5. **Demonstrate automation**: GitHub Actions running the same drill

## 🔧 Configuration

Edit `helm/aswarm-prototype/values.yaml`:
- `noisyReplicas: 50` (scale up targets)
- `policy.ttlSeconds: 60` (faster revert)
- `namespace: demo` (custom namespace)

## 🎯 KPI Targets

- **MTTD P95**: ≤ 200ms (Mean Time To Detect)
- **MTTR P95**: ≤ 5s (Mean Time To Respond)
- **Compliance**: % of runs meeting targets

## 🔐 Security Features

- **Signed certificates** with HMAC-SHA256
- **Policy versioning** with content hashes  
- **Bounded actions** with TTL auto-revert
- **Audit trails** with timestamps and evidence

This upgrade transforms the prototype from a proof-of-concept into an investor-ready demonstration of A-SWARM's core capabilities.