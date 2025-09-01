# A-SWARM Client Deployment Guide

## System Overview

A-SWARM is a Kubernetes-native threat detection system that provides sub-200ms Mean Time To Detect (MTTD) through a dual-path architecture:

### Architecture Components

1. **Sentinel Agents** (DaemonSet)
   - Runs on every Kubernetes node
   - Continuously monitors for anomalies
   - Sends high-confidence alerts via UDP fast-path
   - Maintains audit trail via Kubernetes Leases

2. **Pheromone Coordinator** (Deployment) 
   - Receives UDP fast-path alerts
   - Coordinates cluster-wide threat response
   - Provides centralized logging and metrics

3. **Dual-Path Design**
   - **Fast Path**: UDP packets for <1ms alert delivery
   - **Audit Path**: Kubernetes Leases for compliance/forensics

### Performance Characteristics

- **Detection Speed**: <200ms MTTD (typically <1ms via UDP)
- **Packet Processing**: P95 latency <0.1ms
- **Resource Usage**: 
  - Sentinel: 50m CPU, 128Mi RAM per node
  - Pheromone: 100m CPU, 128Mi RAM total

## Prerequisites

- Kubernetes cluster (1.19+)
- kubectl configured with cluster admin access
- PowerShell (Windows) or Bash (Linux/Mac)

## Installation

### Quick Install
```powershell
# Download deployment files
# Navigate to A-SWARM directory
cd /path/to/A-SWARM/prototype

# Deploy the system
.\deploy\deploy-fastpath.ps1
```

### Clean Install (remove existing)
```powershell
.\deploy\deploy-fastpath.ps1 -Clean
.\deploy\deploy-fastpath.ps1
```

### Verification
```powershell
.\deploy\verify-fastpath-fixed.ps1
```

## System Operation

### Normal Operation
- System runs continuously once deployed
- No manual intervention required
- Self-healing: pods restart automatically if they fail
- Automatic scaling: Sentinel agents deploy to new nodes

### What Happens During Threat Detection
1. Sentinel detects anomaly (score ≥0.90)
2. UDP packet sent to Pheromone (<1ms)
3. Pheromone logs alert and can trigger responses
4. Kubernetes Lease created for audit trail
5. Alert visible in logs immediately

### Monitoring System Health
```bash
# Check all components
kubectl get pods -n aswarm

# Watch real-time logs
kubectl logs -n aswarm -l app=aswarm-pheromone -f  # Alerts
kubectl logs -n aswarm -l app=aswarm-sentinel -f   # Detection

# Check system metrics
kubectl top pods -n aswarm
```

## Troubleshooting

### Common Issues

**Pods not starting**
```bash
kubectl describe pods -n aswarm
kubectl get events -n aswarm --sort-by='.lastTimestamp'
```

**No alerts being generated**
```bash
# Check Sentinel logs for scoring activity
kubectl logs -n aswarm daemonset/aswarm-sentinel

# Verify UDP connectivity
kubectl logs -n aswarm deployment/aswarm-pheromone
```

**Performance issues**
```bash
# Check resource usage
kubectl top pods -n aswarm

# Verify fast-path latency
python scripts/test_fastpath_simple.py
```

### System Restart
```powershell
# Restart all components
kubectl rollout restart deployment/aswarm-pheromone -n aswarm
kubectl rollout restart daemonset/aswarm-sentinel -n aswarm
```

### Complete Removal
```powershell
kubectl delete namespace aswarm
```

## Security Considerations

- **HMAC Authentication**: All UDP packets authenticated with shared key
- **Replay Protection**: Sequence numbers prevent packet replay
- **RBAC**: Minimal Kubernetes permissions (leases, nodes, events only)
- **Namespace Isolation**: Runs in dedicated 'aswarm' namespace
- **Key Management**: Change default key in production:
  ```bash
  kubectl patch secret aswarm-fastpath-key -n aswarm -p '{"stringData":{"key":"your-production-key"}}'
  ```

## Configuration

### Environment Variables
- `ASWARM_FASTPATH_KEY`: UDP authentication key
- `FASTPATH_HOST`: Pheromone service hostname
- `FASTPATH_PORT`: UDP port (default: 8888)
- `NODE_NAME`: Kubernetes node name (auto-detected)

### Tuning Parameters
- Detection threshold: Score ≥0.90 triggers fast-path
- UDP duplicates: 3 sends per packet (reliability)
- Packet cadence: 50ms between signals
- Queue size: 10,000 packets (burst protection)

## Support and Maintenance

### Log Locations
- Pheromone alerts: `kubectl logs -n aswarm deployment/aswarm-pheromone`
- Sentinel activity: `kubectl logs -n aswarm daemonset/aswarm-sentinel`
- Kubernetes events: `kubectl get events -n aswarm`

### Health Checks
- Pods should show "Running" status
- Both Sentinel and Pheromone should have ready replicas
- UDP service should have ClusterIP assigned
- Fast-path test should show <200ms P95 latency

### Scaling
- **Horizontal**: Automatically scales to new nodes (DaemonSet)
- **Vertical**: Adjust resource requests/limits in YAML
- **Multi-cluster**: Deploy independently in each cluster