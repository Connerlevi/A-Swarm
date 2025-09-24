# Simple Docker Desktop Status Check
# Safe to run - no modifications, only checks

Write-Host "=== Docker Desktop Diagnostic ===" -ForegroundColor Cyan
Write-Host ""

# 1. Check WSL
Write-Host "[1] WSL Status:" -ForegroundColor Yellow
wsl --list --verbose

# 2. Check Docker processes
Write-Host "`n[2] Docker Processes:" -ForegroundColor Yellow
Get-Process | Where-Object { $_.ProcessName -match "Docker" } | Select-Object ProcessName, Id | Format-Table

# 3. Check Docker service
Write-Host "`n[3] Docker Service:" -ForegroundColor Yellow
Get-Service | Where-Object { $_.Name -match "docker" } | Format-Table Name, Status, DisplayName

# 4. Check Docker version
Write-Host "`n[4] Docker Version:" -ForegroundColor Yellow
try {
    docker version
} catch {
    Write-Host "Docker command not available" -ForegroundColor Red
}

# 5. Check Kubernetes context
Write-Host "`n[5] Kubernetes Context:" -ForegroundColor Yellow
try {
    kubectl config current-context
    kubectl get nodes
} catch {
    Write-Host "Kubernetes not accessible" -ForegroundColor Red
}

Write-Host "`n=== End Diagnostic ===" -ForegroundColor Cyan