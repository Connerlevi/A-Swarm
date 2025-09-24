# Simple Docker Desktop Kubernetes Recovery Script for A-SWARM
param(
    [switch]$Deep = $false
)

$ErrorActionPreference = "Continue"
Write-Host "=== Docker Desktop Kubernetes Recovery Script ===" -ForegroundColor Cyan

# Check admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Write-Host "WARNING: Not running as Administrator. Some fixes may not work." -ForegroundColor Yellow
}

# Helper function
function Wait-DockerDesktop {
    param([int]$TimeoutSec = 180)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try { 
            docker info | Out-Null
            return $true 
        } catch { 
            Start-Sleep 3 
        }
    }
    return $false
}

# Step 1: WSL Cleanup
Write-Host "`n[1/4] Cleaning WSL state..." -ForegroundColor Green
wsl --terminate docker-desktop 2>$null
wsl --terminate docker-desktop-data 2>$null
Start-Sleep -Seconds 2

# Step 2: Docker Cleanup (if requested)
if ($Deep) {
    Write-Host "`n[2/4] Deep cleaning Docker resources..." -ForegroundColor Green
    docker container prune -f 2>$null
    docker image prune -a -f 2>$null
    docker volume prune -f 2>$null
    docker network prune -f 2>$null
} else {
    Write-Host "`n[2/4] Skipping deep clean (use -Deep to enable)" -ForegroundColor Gray
}

# Step 3: Restart WSL
Write-Host "`n[3/4] Restarting WSL..." -ForegroundColor Green
wsl --shutdown
Start-Sleep -Seconds 3

# Step 4: Start Docker Desktop
Write-Host "`n[4/4] Starting Docker Desktop..." -ForegroundColor Green
$dockerPaths = @(
    "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
    "$env:LocalAppData\Docker\Docker\Docker Desktop.exe"
)
$dockerExe = $dockerPaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($dockerExe) {
    Start-Process $dockerExe | Out-Null
    Write-Host "Waiting for Docker engine..." -ForegroundColor Gray
    if (-not (Wait-DockerDesktop -TimeoutSec 120)) {
        Write-Host "Docker engine failed to start" -ForegroundColor Red
        exit 1
    }
    Write-Host "Docker engine is ready" -ForegroundColor Green
} else {
    Write-Host "Docker Desktop not found" -ForegroundColor Red
    exit 1
}

# Test Docker and kubectl
Write-Host "`n=== Testing Docker and Kubernetes ===" -ForegroundColor Cyan
try {
    docker version --format "Docker: {{.Server.Version}}"
    kubectl version --client=true --short 2>$null
    kubectl cluster-info --request-timeout=5s 2>$null
    Write-Host "Success! Docker Desktop Kubernetes is working" -ForegroundColor Green
} catch {
    Write-Host "Some components may still be starting up" -ForegroundColor Yellow
}

Write-Host "`nRecovery complete!" -ForegroundColor Green