# Minimal Docker Desktop Recovery
# Step-by-step with pauses to avoid crashes

Write-Host "=== Minimal Docker Recovery ===" -ForegroundColor Cyan
Write-Host "This script will restart Docker Desktop step by step" -ForegroundColor Yellow
Write-Host ""

# Step 1: Shutdown WSL
Write-Host "[1/5] Shutting down WSL..." -ForegroundColor Green
wsl --shutdown
Start-Sleep -Seconds 3

# Step 2: Kill Docker processes
Write-Host "[2/5] Stopping Docker processes..." -ForegroundColor Green
Get-Process | Where-Object { $_.ProcessName -match "Docker" } | ForEach-Object {
    Write-Host "  Stopping $($_.ProcessName) (PID: $($_.Id))"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 3

# Step 3: Start Docker Desktop
Write-Host "[3/5] Starting Docker Desktop..." -ForegroundColor Green
$dockerPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dockerPath) {
    Start-Process $dockerPath
    Write-Host "  Docker Desktop launched"
} else {
    Write-Host "  Docker Desktop not found at: $dockerPath" -ForegroundColor Red
    exit 1
}

# Step 4: Wait for Docker
Write-Host "[4/5] Waiting for Docker engine (this may take 60-90 seconds)..." -ForegroundColor Green
$attempts = 0
$maxAttempts = 30
while ($attempts -lt $maxAttempts) {
    $attempts++
    Write-Host "  Attempt $attempts/$maxAttempts..." -NoNewline
    
    try {
        docker version | Out-Null
        Write-Host " Success!" -ForegroundColor Green
        break
    } catch {
        Write-Host " Not ready yet"
        Start-Sleep -Seconds 3
    }
}

if ($attempts -eq $maxAttempts) {
    Write-Host "  Docker did not start in time" -ForegroundColor Red
    exit 1
}

# Step 5: Check Kubernetes
Write-Host "[5/5] Checking Kubernetes..." -ForegroundColor Green
try {
    kubectl config use-context docker-desktop | Out-Null
    kubectl get nodes
    Write-Host "  Kubernetes is ready!" -ForegroundColor Green
} catch {
    Write-Host "  Kubernetes is not enabled or not ready" -ForegroundColor Yellow
    Write-Host "  Enable it in Docker Desktop settings if needed" -ForegroundColor Yellow
}

Write-Host "`n=== Recovery Complete ===" -ForegroundColor Green
Write-Host "Docker Desktop should now be running" -ForegroundColor Cyan