# Production A-SWARM Deployment Script
# Rock-solid deployment with proper code bundling and error handling

param(
    [string]$Namespace = "aswarm",
    [switch]$Clean = $false,
    [switch]$SkipCodeBundle = $false,
    [string]$FastPathKey = ""
)

$ErrorActionPreference = "Stop"

Write-Host "=== A-SWARM Production Deployment ===" -ForegroundColor Cyan
Write-Host ""

# Guard against missing tools
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) { 
    throw "kubectl not found in PATH" 
}
if (-not $SkipCodeBundle -and -not (Get-Command tar.exe -ErrorAction SilentlyContinue)) { 
    throw "tar.exe not found (required for code bundling)" 
}

# Show context
$ctx = kubectl config current-context
if (-not $ctx) { throw "kubectl has no current context" }
Write-Host "kubectl context: $ctx" -ForegroundColor Gray

# Clean up if requested
if ($Clean) {
    Write-Host "Cleaning up existing deployment..." -ForegroundColor Yellow
    kubectl delete namespace $Namespace --ignore-not-found=true 2>$null
    Write-Host "Cleanup complete" -ForegroundColor Green
    Write-Host ""
}

# Create namespace
Write-Host "Creating namespace..." -ForegroundColor Yellow
kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null

# Create fast-path key secret (only if missing)
Write-Host "Creating fast-path key secret..." -ForegroundColor Yellow
if (-not (kubectl get secret aswarm-fastpath-key -n $Namespace > $null 2>&1)) {
    if ($FastPathKey -eq "") {
        $bytes = New-Object byte[] 32
        (New-Object System.Random).NextBytes($bytes)
        $FastPathKey = [Convert]::ToBase64String($bytes)
    }
    kubectl create secret generic aswarm-fastpath-key -n $Namespace --from-literal=key=$FastPathKey | Out-Null
    Write-Host "[OK] Created new fast-path key" -ForegroundColor Green
} else {
    Write-Host "[OK] Using existing fast-path key" -ForegroundColor Green
}

# Create code bundle
if (-not $SkipCodeBundle) {
    Write-Host ""
    Write-Host "Creating code bundle..." -ForegroundColor Yellow
    
    # Check required directories
    if (-not (Test-Path "sentinel") -or -not (Test-Path "pheromone")) {
        throw "sentinel/ and pheromone/ directories not found. Run from prototype directory."
    }
    
    # Always produce tar.gz using Windows tar.exe (bsdtar)
    $tarFile = "aswarm-code.tar.gz"
    if (Test-Path $tarFile) { Remove-Item $tarFile -Force }
    
    & tar.exe -czf $tarFile sentinel pheromone
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
    
    # Fail fast if bundle too large for Secret (~1MiB)
    $size = (Get-Item $tarFile).Length
    if ($size -gt 1000000) {
        throw "Code bundle is $size bytes (>1MiB). Use image build or split Secrets."
    }
    
    # Print bundle hash for tracking
    $hash = (Get-FileHash $tarFile -Algorithm SHA256).Hash
    Write-Host "Code bundle sha256: $hash" -ForegroundColor Gray
    
    # Idempotent upsert of Secret from file (no BOM issues)
    kubectl delete secret aswarm-code-bundle -n $Namespace --ignore-not-found | Out-Null
    kubectl create secret generic aswarm-code-bundle -n $Namespace --from-file=code.tar.gz=$tarFile | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Secret creation failed" }
    
    # Annotate with hash
    kubectl annotate secret aswarm-code-bundle -n $Namespace "aswarm.ai/bundle-sha=$hash" --overwrite | Out-Null
    
    Remove-Item $tarFile
    Write-Host "[OK] Code bundle created ($([math]::Round($size/1024))KB)" -ForegroundColor Green
}

# Deploy the manifest
Write-Host ""
Write-Host "Deploying A-SWARM components..." -ForegroundColor Yellow

$Manifest = "deploy/production-ready.yaml"
if (-not (Test-Path $Manifest)) {
    throw "Manifest $Manifest not found"
}

try {
    kubectl apply -f $Manifest
    if ($LASTEXITCODE -ne 0) { throw "kubectl apply failed" }
    
    Write-Host "[OK] Manifest applied" -ForegroundColor Green
    
    # Wait for rollout using kubectl rollout status
    Write-Host ""
    Write-Host "Waiting for rollout..." -ForegroundColor Yellow
    
    kubectl -n $Namespace rollout status deploy/aswarm-pheromone --timeout=120s
    if ($LASTEXITCODE -ne 0) { throw "Pheromone rollout failed" }
    
    kubectl -n $Namespace rollout status ds/aswarm-sentinel --timeout=180s
    if ($LASTEXITCODE -ne 0) { throw "Sentinel rollout failed" }
    
    Write-Host "[OK] All components ready" -ForegroundColor Green
    
} catch {
    Write-Host ""
    Write-Host "[ERROR] Deployment failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Tip: run 'kubectl -n $Namespace describe pods' for details" -ForegroundColor Yellow
    exit 1
}

# Show final status
Write-Host ""
Write-Host "=== Deployment Status ===" -ForegroundColor Cyan
kubectl get pods -n $Namespace

# DaemonSet status with ready/desired
$ds = kubectl get daemonset -n $Namespace aswarm-sentinel -o json 2>$null | ConvertFrom-Json
if ($ds) {
    $ready = $ds.status.numberReady
    $desired = $ds.status.desiredNumberScheduled
    Write-Host "[OK] Sentinel $ready/$desired nodes ready" -ForegroundColor Green
}

Write-Host ""
kubectl get svc -n $Namespace

# Health check using curl pod (no background jobs)
Write-Host ""
Write-Host "=== Health Check ===" -ForegroundColor Cyan
Write-Host "Checking Pheromone health..." -ForegroundColor Yellow

kubectl -n $Namespace run curltmp --rm -i --restart=Never --image=curlimages/curl -- curl -fsS http://aswarm-pheromone:9000/healthz > $null 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Pheromone health check passed" -ForegroundColor Green
} else {
    Write-Host "[WARN] Pheromone health check failed" -ForegroundColor Yellow
}

# Final instructions
Write-Host ""
Write-Host "=== Production Deployment Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Monitor: kubectl logs -n $Namespace -f -l app=aswarm-pheromone"
Write-Host "2. Metrics: kubectl port-forward -n $Namespace svc/aswarm-pheromone 9000:9000"
Write-Host "3. Test: python scripts/test_fastpath_simple.py"
Write-Host "4. UI: Start building the React dashboard"
Write-Host ""
Write-Host "Clean up: .\deploy\deploy-production.ps1 -Clean" -ForegroundColor Gray