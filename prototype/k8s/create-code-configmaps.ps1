# Create ConfigMaps with Python code for A-SWARM components

$NAMESPACE = if ($env:NAMESPACE) { $env:NAMESPACE } else { "aswarm" }

Write-Host "Creating ConfigMaps with Python code in namespace: $NAMESPACE"

# Create empty __init__.py file
$null | Out-File -FilePath "__init__.py"

# Create Pheromone code ConfigMap
Write-Host "Creating aswarm-pheromone-code..."

# First, check if we have the signal_types.py file
if (-not (Test-Path "../pheromone/signal_types.py")) {
    Write-Host "Creating minimal signal_types.py..."
    @'
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

@dataclass
class LeaseSignal:
    node: str
    seq: int
    score: float
    elevate: bool
    client_ts: Optional[datetime] = None
    server_ts: Optional[datetime] = None
    elevate_ts: Optional[datetime] = None
    run_id: Optional[str] = None

@dataclass
class QuorumMetrics:
    witness_count: int
    total_samples: int
    mean_score: float
    p95_score: float
    window_start_ts: datetime
    window_end_ts: datetime

@dataclass
class ElevationEvent:
    timestamp: datetime
    witness_count: int
    mean_score: float
    p95_score: float
    threshold: int
    window_ms: int
    reason: str
    run_id: Optional[str] = None
'@ | Out-File -FilePath "../pheromone/signal_types.py"
}

kubectl create configmap aswarm-pheromone-code `
  --namespace=$NAMESPACE `
  --from-file=gossip_v2.py=../pheromone/gossip_v2.py `
  --from-file=signal_types.py=./pheromone/signal_types.py `
  --from-file=udp_listener.py=./pheromone/udp_listener.py `
  --from-file=__init__.py=./pheromone/__init__.py `
  --dry-run=client -o yaml | kubectl apply -f -

# Create Sentinel code ConfigMap  
Write-Host "Creating aswarm-sentinel-code..."
kubectl create configmap aswarm-sentinel-code `
  --namespace=$NAMESPACE `
  --from-file=telemetry_v2.py=../sentinel/telemetry_v2.py `
  --from-file=fast_path.py=./sentinel/fast_path.py `
  --from-file=__init__.py=./sentinel/__init__.py `
  --dry-run=client -o yaml | kubectl apply -f -

# Clean up
Remove-Item -Path "__init__.py" -ErrorAction SilentlyContinue

Write-Host "ConfigMaps created successfully!"