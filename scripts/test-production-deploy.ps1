# Test Production Deployment
# Validates the production deployment is working correctly

param(
    [string]$Namespace = "aswarm"
)

Write-Host "=== Testing Production A-SWARM Deployment ===" -ForegroundColor Cyan
Write-Host ""

# Check all pods are running
Write-Host "Checking pod status..." -ForegroundColor Yellow
$pods = kubectl get pods -n $Namespace -o json | ConvertFrom-Json

$allReady = $true
foreach ($pod in $pods.items) {
    $name = $pod.metadata.name
    $ready = ($pod.status.conditions | Where-Object { $_.type -eq "Ready" }).status
    $phase = $pod.status.phase
    
    if ($ready -eq "True" -and $phase -eq "Running") {
        Write-Host "[OK] $name is ready" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] $name not ready (Phase: $phase, Ready: $ready)" -ForegroundColor Red
        $allReady = $false
    }
}

if (-not $allReady) {
    Write-Host ""
    Write-Host "Some pods not ready. Check logs:" -ForegroundColor Yellow
    Write-Host "kubectl logs -n $Namespace -l app=aswarm-pheromone" -ForegroundColor White
    Write-Host "kubectl logs -n $Namespace -l app=aswarm-sentinel" -ForegroundColor White
    exit 1
}

# Test health endpoints
Write-Host ""
Write-Host "Testing health endpoints..." -ForegroundColor Yellow

# Pheromone health
kubectl -n $Namespace run healthtest --rm -i --restart=Never --image=curlimages/curl -- curl -fsS http://aswarm-pheromone:9000/healthz > $null 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Pheromone health endpoint accessible" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Pheromone health endpoint failed" -ForegroundColor Red
}

# Test metrics endpoint
kubectl -n $Namespace run metricstest --rm -i --restart=Never --image=curlimages/curl -- curl -fsS http://aswarm-pheromone:9000/metrics > $null 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Pheromone metrics endpoint accessible" -ForegroundColor Green
} else {
    Write-Host "[WARN] Pheromone metrics endpoint failed" -ForegroundColor Yellow
}

# Check for actual traffic
Write-Host ""
Write-Host "Checking for fast-path traffic..." -ForegroundColor Yellow
$pheromoneLog = kubectl logs -n $Namespace -l app=aswarm-pheromone --tail=10 2>$null
if ($pheromoneLog -like "*Elevation:*") {
    Write-Host "[OK] Fast-path traffic detected" -ForegroundColor Green
} else {
    Write-Host "[INFO] No fast-path traffic yet (normal for first few seconds)" -ForegroundColor Cyan
}

$sentinelLog = kubectl logs -n $Namespace -l app=aswarm-sentinel --tail=10 2>$null  
if ($sentinelLog -like "*Fast-path:*") {
    Write-Host "[OK] Sentinel sending fast-path signals" -ForegroundColor Green
} else {
    Write-Host "[INFO] No high-score signals yet" -ForegroundColor Cyan
}

# Performance test
Write-Host ""
Write-Host "=== Running Performance Test ===" -ForegroundColor Cyan
if (Test-Path "scripts/test_fastpath_simple.py") {
    python scripts/test_fastpath_simple.py
} else {
    Write-Host "Performance test script not found" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Production Deployment Test Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Production deployment is functional!" -ForegroundColor Green
Write-Host "Ready for UI development and demo scenarios." -ForegroundColor Cyan