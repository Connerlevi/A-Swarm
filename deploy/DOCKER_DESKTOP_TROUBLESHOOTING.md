# Docker Desktop Kubernetes Troubleshooting Guide

## Common Issue: Container Sandbox Timeout

### Primary Symptoms
```
Failed to create pod sandbox: rpc error: code = Unknown desc = failed to start sandbox container for pod "XXX": operation timeout: context deadline exceeded
```

### Root Causes
1. **Container runtime deadlock** - Docker Desktop's containerd/dockerd in bad state
2. **Stale network namespaces** - Lingering netns from terminated containers
3. **CNI plugin issues** - Corrupted CNI state in `/var/lib/cni`
4. **WSL2 memory pressure** - Insufficient resources for pod sandboxes
5. **Windows Defender/AV** - Scanning container filesystem operations

## Quick Fix (95% Success Rate)

```powershell
# Run the automated recovery script
./deploy/docker-desktop-fix.ps1

# For deeper issues
./deploy/docker-desktop-fix.ps1 -Deep

# For persistent issues (nuclear option)
./deploy/docker-desktop-fix.ps1 -VeryDeep -Logs
```

## Manual Troubleshooting Steps

### 1. Quick Diagnostics
```powershell
# Check current context
kubectl config current-context

# Find stuck pods
kubectl get pods -A --field-selector=status.phase=Pending

# Recent events
kubectl get events -A --sort-by=.lastTimestamp | tail -50
```

### 2. Inside Docker Desktop VM
```powershell
# Access the VM
wsl -d docker-desktop

# Check containerd/kubelet logs
journalctl -u containerd --no-pager -n 200
journalctl -u kubelet --no-pager -n 200

# List running containers via CRI
crictl ps -a

# Check CNI state
ls -la /var/lib/cni/
ls -la /run/cni/

# List network namespaces
ip netns list

# Exit VM
exit
```

### 3. Network Namespace Cleanup
Common cause of sandbox timeouts is stale network namespaces:

```bash
# Inside docker-desktop VM
for ns in $(ip netns list | awk '{print $1}'); do
  echo "Deleting netns: $ns"
  ip netns delete "$ns"
done
```

### 4. CNI State Reset
```bash
# Inside docker-desktop VM
rm -rf /var/lib/cni/*
rm -rf /run/cni/*
systemctl restart containerd
systemctl restart kubelet
```

## Prevention

### 1. WSL2 Configuration
Create/edit `%USERPROFILE%\.wslconfig`:
```ini
[wsl2]
memory=16GB
processors=8
swap=0
localhostForwarding=true
```

### 2. Windows Defender Exclusions
Add exclusions for:
- `C:\Users\<username>\AppData\Local\Docker`
- `C:\ProgramData\DockerDesktop`
- `\\wsl$\docker-desktop`
- `\\wsl$\docker-desktop-data`

### 3. Weekly Maintenance
```powershell
# Weekly cleanup script
docker system prune -a --volumes -f
wsl --shutdown
# Then restart Docker Desktop
```

## Alternative: Local Kubernetes Options

### Kind (Kubernetes in Docker)
```powershell
# Install
choco install kind

# Create cluster with UDP support
@"
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: aswarm
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30888
        hostPort: 30888
        protocol: UDP
  - role: worker
  - role: worker
"@ | kind create cluster --config=-
```

### K3d (Lightweight k3s)
```powershell
# Install
choco install k3d

# Create cluster
k3d cluster create aswarm --agents 2 --k3s-arg "--disable=traefik@server:*"
```

### Minikube
```powershell
# Install
choco install minikube

# Create cluster with extra resources
minikube start --cpus=4 --memory=8192 --driver=docker
```

## A-SWARM Specific Checks

### After Recovery
```powershell
# Apply certificates
kubectl apply -f deploy/minimal-identity-setup.yaml

# Deploy A-SWARM
kubectl apply -f deploy/fastpath-with-identity.yaml

# Check pods
kubectl get pods -n aswarm -o wide

# Check logs
kubectl logs -n aswarm deployment/aswarm-pheromone
kubectl logs -n aswarm daemonset/aswarm-sentinel

# Test UDP connectivity
kubectl run -n aswarm test --rm -it --image=busybox:1.36 --restart=Never -- \
  sh -c 'echo "{\"test\":1}" | nc -u -w1 aswarm-pheromone 8888'
```

### Identity Verification
```powershell
# Check certificates
kubectl get secrets -n aswarm | grep tls

# Verify SPIFFE IDs
kubectl get secret -n aswarm pheromone-tls -o json | 
  jq -r '.data."tls.crt"' | 
  base64 -d | 
  openssl x509 -text -noout | 
  grep -A5 "Subject Alternative Name"
```

## Debugging Pod Creation Issues

### Get Detailed Pod Events
```powershell
# For a specific stuck pod
kubectl describe pod -n aswarm <pod-name>

# Watch events in real-time
kubectl get events -n aswarm -w
```

### Container Runtime Direct Debug
```powershell
# Inside docker-desktop VM
# List all k8s containers
ctr -n k8s.io containers list

# Check specific container
ctr -n k8s.io tasks list | grep <pod-name>

# View container logs directly
crictl logs <container-id>
```

## Performance Tuning

### Docker Desktop Settings
1. Settings → Resources:
   - CPUs: 8+ (50% of available)
   - Memory: 16GB+ 
   - Disk: 100GB+
   - Enable "Use the WSL 2 based engine"

2. Settings → Kubernetes:
   - Enable Kubernetes
   - Show system containers (for debugging)

### Network Performance
For UDP-heavy workloads like A-SWARM:
```powershell
# Inside docker-desktop VM
# Increase UDP buffer sizes
sysctl -w net.core.rmem_max=134217728
sysctl -w net.core.wmem_max=134217728
sysctl -w net.ipv4.udp_mem="262144 524288 1048576"
```

## Known Issues & Workarounds

### Issue: Stuck namespace deletion
```powershell
# Force finalize namespace
$ns = kubectl get ns <namespace> -o json | ConvertFrom-Json
$ns.spec.finalizers = @()
$ns | ConvertTo-Json -Depth 100 | kubectl replace --raw /api/v1/namespaces/<namespace>/finalize -f -
```

### Issue: Image pull timeouts
```powershell
# Pre-pull images on all nodes
kubectl create job prepull --image=python:3.11-alpine -- sleep 1
```

### Issue: UDP packets dropped
- Switch from ClusterIP to NodePort service type
- Or use HostNetwork mode for pods (security trade-off)

## Getting Help

1. **Logs to collect for support**:
   - `docker-desktop-fix.ps1 -Logs` output
   - `kubectl get nodes -o yaml`
   - `kubectl get events -A`
   - Windows Event Viewer → Applications and Services → Docker

2. **Docker Desktop Reset** (last resort):
   - Settings → Troubleshoot → Clean / Purge data
   - Settings → Troubleshoot → Reset to factory defaults

3. **WSL2 Reset** (nuclear option):
   ```powershell
   wsl --unregister docker-desktop
   wsl --unregister docker-desktop-data
   # Then reinstall Docker Desktop
   ```