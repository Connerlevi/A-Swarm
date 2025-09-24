# A-SWARM Fast-Path Deployment Script
# Clean, reliable deployment with proper error handling

param(
    [string]$Namespace = "aswarm",
    [switch]$Clean = $false,
    [switch]$Test = $false
)

$ErrorActionPreference = "Stop"

Write-Host "=== A-SWARM Fast-Path Deployment ===" -ForegroundColor Cyan
Write-Host ""

# Function to check if a command succeeded
function Test-LastCommand {
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Command failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
}

# Clean up if requested
if ($Clean) {
    Write-Host "Cleaning up existing deployment..." -ForegroundColor Yellow
    kubectl delete namespace $Namespace --ignore-not-found=true 2>$null
    Write-Host "Cleanup complete" -ForegroundColor Green
    Write-Host ""
}

# Check kubectl is available
Write-Host "Checking prerequisites..." -ForegroundColor Yellow
$kubectl = Get-Command kubectl -ErrorAction SilentlyContinue
if (-not $kubectl) {
    Write-Host "Error: kubectl not found in PATH" -ForegroundColor Red
    exit 1
}

# Check cluster connectivity
kubectl cluster-info > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Cannot connect to Kubernetes cluster" -ForegroundColor Red
    Write-Host "Please ensure kubectl is configured correctly" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Kubernetes cluster accessible" -ForegroundColor Green

# Deploy the complete solution
Write-Host ""
Write-Host "Deploying A-SWARM Fast-Path..." -ForegroundColor Yellow

# Apply the all-in-one manifest
kubectl apply -f deploy/fastpath-complete.yaml
Test-LastCommand

Write-Host "[OK] Resources created" -ForegroundColor Green

# Wait for pods to be ready
Write-Host ""
Write-Host "Waiting for pods to start..." -ForegroundColor Yellow

# Wait for Pheromone
$retries = 30
while ($retries -gt 0) {
    $ready = kubectl get deployment -n $Namespace aswarm-pheromone -o jsonpath='{.status.readyReplicas}' 2>$null
    if ($ready -eq "1") {
        break
    }
    Write-Host "." -NoNewline
    Start-Sleep -Seconds 2
    $retries--
}
Write-Host ""

if ($retries -eq 0) {
    Write-Host "Warning: Pheromone deployment not ready after 60 seconds" -ForegroundColor Yellow
} else {
    Write-Host "[OK] Pheromone ready" -ForegroundColor Green
}

# Wait for Sentinel
$sentinelReady = $false
$retries = 15
while ($retries -gt 0) {
    $desired = kubectl get daemonset -n $Namespace aswarm-sentinel -o jsonpath='{.status.desiredNumberScheduled}' 2>$null
    $ready = kubectl get daemonset -n $Namespace aswarm-sentinel -o jsonpath='{.status.numberReady}' 2>$null
    
    if ($desired -eq $ready -and $desired -ne "0") {
        $sentinelReady = $true
        break
    }
    Write-Host "." -NoNewline
    Start-Sleep -Seconds 2
    $retries--
}
Write-Host ""

if ($sentinelReady) {
    Write-Host "[OK] Sentinel ready" -ForegroundColor Green
} else {
    Write-Host "Warning: Sentinel DaemonSet not fully ready" -ForegroundColor Yellow
}

# Show deployment status
Write-Host ""
Write-Host "=== Deployment Status ===" -ForegroundColor Cyan
kubectl get pods -n $Namespace
Write-Host ""
kubectl get svc -n $Namespace

# Run test if requested
if ($Test) {
    Write-Host ""
    Write-Host "=== Running Fast-Path Test ===" -ForegroundColor Cyan
    
    # Get Pheromone service IP
    $pheromoneIP = kubectl get svc -n $Namespace aswarm-pheromone -o jsonpath='{.spec.clusterIP}'
    
    if ($pheromoneIP) {
        Write-Host "Pheromone service IP: $pheromoneIP" -ForegroundColor Green
        
        # Check if test script exists
        if (Test-Path "scripts/test_fast_path.py") {
            Write-Host "Running latency test..."
            python scripts/test_fast_path.py --mode loopback --packets 50 --key "aswarm-demo-fastpath-key-please-change-in-production"
        } else {
            Write-Host "Test script not found" -ForegroundColor Yellow
        }
    }
}

# Show logs
Write-Host ""
Write-Host "=== Recent Logs ===" -ForegroundColor Cyan
Write-Host "Pheromone logs:" -ForegroundColor Yellow
kubectl logs -n $Namespace deployment/aswarm-pheromone --tail=5 2>$null

Write-Host ""
Write-Host "Sentinel logs:" -ForegroundColor Yellow
kubectl logs -n $Namespace daemonset/aswarm-sentinel --tail=5 2>$null

# Final instructions
Write-Host ""
Write-Host "=== Deployment Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Check pod status: kubectl get pods -n $Namespace"
Write-Host "2. View logs: kubectl logs -n $Namespace <pod-name>"
Write-Host "3. Run full test: python scripts/test_fast_path.py"
Write-Host ""
Write-Host "To clean up: .\deploy\deploy-fastpath.ps1 -Clean" -ForegroundColor Gray