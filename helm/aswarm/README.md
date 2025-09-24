# A-SWARM Helm Chart

This Helm chart deploys the A-SWARM autonomic defense system on a Kubernetes cluster.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.8+
- RBAC enabled on the cluster
- (Optional) Prometheus Operator for monitoring

## Installation

### Add the repository (when published)
```bash
helm repo add aswarm https://charts.aswarm.ai
helm repo update
```

### Install from local directory
```bash
# Install with default values (dry-run mode)
helm install aswarm ./helm/aswarm --namespace aswarm --create-namespace

# Install in live mode (enforcement enabled)
helm install aswarm ./helm/aswarm \
  --namespace aswarm \
  --create-namespace \
  --set global.dryRun=false

# Install with custom values file
helm install aswarm ./helm/aswarm \
  --namespace aswarm \
  --create-namespace \
  --values my-values.yaml
```

## Configuration

Key configuration options:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `global.enabled` | Master switch for all components | `true` |
| `global.dryRun` | Log actions without executing | `true` |
| `global.maxRing` | Maximum enforcement ring (1-5) | `3` |
| `sentinel.enabled` | Enable Sentinel DaemonSet | `true` |
| `sentinel.telemetry.cadenceMs` | Signal emission interval | `50` |
| `pheromone.enabled` | Enable Pheromone deployment | `true` |
| `pheromone.replicas` | Number of Pheromone replicas | `3` |
| `pheromone.detection.windowMs` | Detection window size | `80` |
| `pheromone.detection.quorumThreshold` | Minimum witnesses for elevation | `2` |
| `microact.enabled` | Enable MicroAct executor | `true` |
| `microact.enforcement.maxRing` | Maximum ring for enforcement | `3` |
| `monitoring.prometheus.enabled` | Enable Prometheus metrics | `true` |
| `demo.enabled` | Deploy demo workloads | `false` |

See [values.yaml](values.yaml) for complete configuration options.

## Quick Start

1. **Install in dry-run mode** (recommended for initial deployment):
```bash
helm install aswarm ./helm/aswarm \
  --namespace aswarm \
  --create-namespace
```

2. **Verify installation**:
```bash
# Check pods are running
kubectl get pods -n aswarm

# Check kill switch status
kubectl get configmap aswarm-killswitch -n aswarm -o yaml
```

3. **Run a test** (with demo workloads):
```bash
helm upgrade aswarm ./helm/aswarm \
  --namespace aswarm \
  --set demo.enabled=true

# Trigger anomaly manually
kubectl create job --from=cronjob/aswarm-anomaly-test test-1 -n aswarm
```

4. **Monitor the results**:
```bash
# Watch Pheromone logs
kubectl logs -n aswarm -l app.kubernetes.io/component=pheromone -f

# Check for elevation events
kubectl get configmaps -n aswarm -l type=elevation
```

## Kill Switch

The system includes a global kill switch for emergency shutdown:

### Disable all enforcement:
```bash
kubectl patch configmap aswarm-killswitch \
  -n aswarm \
  --type merge -p '{"data":{"state":"disabled"}}'
```

### Re-enable enforcement:
```bash
kubectl patch configmap aswarm-killswitch \
  -n aswarm \
  --type merge -p '{"data":{"state":"enabled"}}'
```

## Monitoring

### Prometheus Metrics

If Prometheus is enabled, metrics are exposed on port 9090:

```bash
# Port-forward to view metrics
kubectl port-forward -n aswarm svc/aswarm-pheromone-metrics 9090:9090

# View metrics at http://localhost:9090/metrics
```

Available metrics:
- `aswarm_detections_total` - Total anomaly detections
- `aswarm_elevations_total` - Total elevation decisions
- `aswarm_actions_total` - Total enforcement actions by ring
- `aswarm_mttd_seconds` - Mean time to detect histogram
- `aswarm_mttr_seconds` - Mean time to respond histogram

### Grafana Dashboard

Import the dashboard from `dashboards/aswarm-overview.json` for visualization.

## Security Considerations

1. **RBAC**: The chart creates least-privilege RBAC rules. Review before deployment.

2. **Network Policies**: Enable with `networkPolicies.enabled=true` to restrict traffic.

3. **Pod Security Standards**: Enforces `restricted` standards by default.

4. **Image Security**: 
   - Images are signed with cosign
   - SBOMs included
   - Run `cosign verify` before deployment

5. **Secrets**: Store sensitive data (HMAC keys) in Kubernetes Secrets, not values.

## Troubleshooting

### Pods not starting
```bash
# Check pod events
kubectl describe pod -n aswarm -l app.kubernetes.io/name=aswarm

# Check RBAC permissions
kubectl auth can-i --list --as=system:serviceaccount:aswarm:aswarm
```

### No detections occurring
```bash
# Check kill switch
kubectl get cm aswarm-killswitch -n aswarm -o jsonpath='{.data.state}'

# Verify Sentinel is emitting
kubectl logs -n aswarm -l app.kubernetes.io/component=sentinel --tail=50
```

### Actions not executing
```bash
# Check dry-run mode
kubectl get cm aswarm-killswitch -n aswarm -o jsonpath='{.data.dryrun\.enabled}'

# Check max ring setting
kubectl logs -n aswarm -l app.kubernetes.io/component=microact | grep "ring"
```

## Uninstall

```bash
# Remove the deployment
helm uninstall aswarm -n aswarm

# Remove the namespace (and all resources)
kubectl delete namespace aswarm

# Note: NetworkPolicies created by MicroAct in other namespaces
# must be cleaned up manually
```

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for development guidelines.

## License

Copyright (c) 2025 A-SWARM Project. All rights reserved.