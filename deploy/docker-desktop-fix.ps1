# Docker Desktop Kubernetes Recovery Script for A-SWARM
# Fixes "failed to start sandbox container" and similar container runtime issues
# Run as Administrator for best results

param(
    [switch]$Deep = $false,
    [switch]$VeryDeep = $false,
    [switch]$SkipPrompt = $false,
    [switch]$RestartAswarmPods = $false,
    [switch]$Logs = $false
)

$ErrorActionPreference = "Continue"
Write-Host "=== Docker Desktop Kubernetes Recovery Script ===" -ForegroundColor Cyan
Write-Host "This script will fix container sandbox timeout issues" -ForegroundColor Yellow
Write-Host ""

# Check if running as Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin -and -not $SkipPrompt) {
    Write-Host "Re-launching elevated..." -ForegroundColor Yellow
    $args = $PSBoundParameters.GetEnumerator() | ForEach-Object { 
        if ($_.Value -is [bool] -and $_.Value) { 
            "-$($_.Key)" 
        } else { 
            "-$($_.Key) $($_.Value)" 
        } 
    }
    Start-Process pwsh -Verb RunAs -ArgumentList "-File `"$PSCommandPath`" $($args -join ' ')"
    exit
}
if (-not $isAdmin) {
    Write-Host "WARNING: Not running as Administrator. Some fixes may not work." -ForegroundColor Yellow
}

# Helper Functions
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

function Wait-Kubernetes {
    param([int]$TimeoutSec = 180)
    try {
        # More reliable than JSON parsing loops
        kubectl wait --for=condition=Ready nodes --all --timeout="${TimeoutSec}s" | Out-Null
        return $true
    } catch { 
        return $false 
    }
}

function Invoke-InDockerVM {
    param([string]$Script)
    $Script | wsl -d docker-desktop sh -s
}

# Step 1: Quick Diagnostics
Write-Host "[1/7] Running diagnostics..." -ForegroundColor Green
$context = kubectl config current-context 2>$null
Write-Host "Current context: $context"

if ($context -ne "docker-desktop") {
    Write-Host "Switching to docker-desktop context..." -ForegroundColor Yellow
    kubectl config use-context docker-desktop
}

# Check if Kubernetes is enabled
if (-not (kubectl cluster-info 2>$null)) {
    Write-Host "Kubernetes appears disabled in Docker Desktop. Enable it in Settings → Kubernetes." -ForegroundColor Yellow
}

# Check for stuck pods
$stuckPods = kubectl get pods -A --field-selector=status.phase=Pending -o json 2>$null | ConvertFrom-Json
if ($stuckPods.items.Count -gt 0) {
    Write-Host "Found $($stuckPods.items.Count) stuck pods" -ForegroundColor Yellow
}

# Step 2: WSL Cleanup
Write-Host "`n[2/7] Cleaning WSL state..." -ForegroundColor Green
Write-Host "Terminating WSL distros..."
wsl --terminate docker-desktop *>$null
wsl --terminate docker-desktop-data *>$null
Start-Sleep -Seconds 2

# Step 3: Docker Cleanup (if requested)
if ($Deep -or $VeryDeep) {
    Write-Host "`n[3/7] Deep cleaning Docker resources..." -ForegroundColor Green
    docker container prune -f 2>$null
    docker image prune -a -f 2>$null
    docker volume prune -f 2>$null
    docker network prune -f 2>$null
    Write-Host "Docker resources cleaned"
} else {
    Write-Host "`n[3/7] Skipping deep clean (use -Deep flag to enable)" -ForegroundColor Gray
}

# Step 4: Clean CNI and containerd state
Write-Host "`n[4/7] Container runtime cleanup..." -ForegroundColor Green

# Diagnostic script
$diagScript = @'
echo "=== containerd & kubelet last 200 lines ==="
journalctl -u containerd -n 200 --no-pager || true
journalctl -u kubelet -n 200 --no-pager || true
echo "=== crictl/ctr snapshot ==="
which crictl >/dev/null 2>&1 && crictl ps -a || true
ctr -n k8s.io tasks ls 2>/dev/null | head -n 20 || true
'@

# Standard cleanup script
$cleanupScript = @'
set -e
echo "Cleaning CNI caches..."
rm -rf /var/lib/cni/* /run/cni/* 2>/dev/null || true
# If netns linger (common), remove them safely
if command -v ip >/dev/null 2>&1; then
  for ns in $(ip netns list 2>/dev/null | awk "{print \$1}"); do
    echo "Deleting stale netns: $ns"
    ip netns delete "$ns" || true
  done
fi
echo "Restarting services..."
systemctl restart containerd 2>/dev/null || /etc/init.d/containerd restart 2>/dev/null || true
systemctl restart kubelet 2>/dev/null || /etc/init.d/kubelet restart 2>/dev/null || true
echo "Done."
'@

# Very deep cleanup script (use with caution)
$veryDeepScript = @'
echo "=== VERY DEEP CLEAN - Backing up CNI configs ==="
mkdir -p /tmp/cni-backup
cp -r /etc/cni/net.d/* /tmp/cni-backup/ 2>/dev/null || true
echo "Removing all pods via CRI..."
crictl rmp -fa 2>/dev/null || true
echo "Cleaning kubelet state..."
rm -rf /var/lib/kubelet/pods/* 2>/dev/null || true
echo "Deep clean complete. CNI configs backed up to /tmp/cni-backup"
'@

if ($Logs) {
    Write-Host "Capturing pre-clean diagnostics..." -ForegroundColor Gray
    Invoke-InDockerVM $diagScript | Tee-Object -FilePath "$env:TEMP\ddesk-preclean-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
}

Write-Host "Cleaning container runtime state..." -ForegroundColor Gray
Invoke-InDockerVM $cleanupScript | Tee-Object -FilePath "$env:TEMP\ddesk-clean.log" | Out-Null

if ($VeryDeep) {
    Write-Host "Running VERY DEEP clean (this may require reconfiguration)..." -ForegroundColor Yellow
    Invoke-InDockerVM $veryDeepScript | Tee-Object -FilePath "$env:TEMP\ddesk-verydeep.log"
}

# Step 5: Restart WSL
Write-Host "`n[5/7] Restarting WSL subsystem..." -ForegroundColor Green
wsl --shutdown
Start-Sleep -Seconds 3

# Step 6: Start Docker Desktop
Write-Host "`n[6/7] Starting Docker Desktop..." -ForegroundColor Green
$dockerPathCandidates = @(
    "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
    "$env:LocalAppData\Docker\Docker\Docker Desktop.exe",
    "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe"
)
$dockerExe = $dockerPathCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($dockerExe) {
    Start-Process $dockerExe | Out-Null
    Write-Host "Waiting for Docker engine..." -ForegroundColor Gray
    if (-not (Wait-DockerDesktop -TimeoutSec 240)) {
        Write-Host "✗ Docker engine failed to start" -ForegroundColor Red
        Write-Host "Try manually starting Docker Desktop and re-run this script" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "✓ Docker engine is ready" -ForegroundColor Green
} else {
    Write-Host "Docker Desktop not found in standard paths:" -ForegroundColor Red
    $dockerPathCandidates | ForEach-Object { Write-Host "  - $_" -ForegroundColor Gray }
    exit 1
}

# Step 7: Verify Kubernetes
Write-Host "`n[7/7] Verifying Kubernetes cluster..." -ForegroundColor Green
if (Wait-Kubernetes -TimeoutSec 240) {
    Write-Host "✓ Kubernetes is ready!" -ForegroundColor Green
} else {
    Write-Host "✗ Kubernetes did not become ready in time" -ForegroundColor Red
    Write-Host "`nNode status:" -ForegroundColor Yellow
    kubectl get nodes -o wide
    Write-Host "`nSystem pods:" -ForegroundColor Yellow
    kubectl get pods -n kube-system
    Write-Host "`nRecent events:" -ForegroundColor Yellow
    kubectl get events -A --sort-by=.lastTimestamp | Select-Object -Last 20
    exit 1
}

# Final verification with test pod
Write-Host "`n=== Running Pod Creation Test ===" -ForegroundColor Cyan
kubectl delete pod test-recovery --ignore-not-found --now 2>$null | Out-Null
kubectl run test-recovery --image=busybox:1.36 --restart=Never --command -- sh -c 'echo PodOK && sleep 1' | Out-Null
$testStarted = $true

try {
    kubectl wait pod/test-recovery --for=condition=Ready --timeout=60s 2>$null | Out-Null
} catch { 
    Start-Sleep 2 # Ready may never be true for short pods; still fine
}

$logs = kubectl logs pod/test-recovery 2>$null
if ($logs -match "PodOK") {
    Write-Host "✓ Pod creation test PASSED" -ForegroundColor Green
} else {
    Write-Host "✗ Pod creation test FAILED" -ForegroundColor Red
    kubectl describe pod test-recovery
    Write-Host "`nRecent events:" -ForegroundColor Yellow
    kubectl get events -A --sort-by=.lastTimestamp | Select-Object -Last 50
}
kubectl delete pod test-recovery --now 2>$null | Out-Null

# Check A-SWARM namespace
Write-Host "`n=== Checking A-SWARM Status ===" -ForegroundColor Cyan
$aswarmPods = kubectl get pods -n aswarm -o json 2>$null | ConvertFrom-Json
if ($aswarmPods.items.Count -gt 0) {
    Write-Host "Found $($aswarmPods.items.Count) A-SWARM pods:"
    kubectl get pods -n aswarm -o wide
    
    # Check for stuck pods
    $stuckAswarmPods = $aswarmPods.items | Where-Object { $_.status.phase -eq "Pending" }
    if ($stuckAswarmPods.Count -gt 0 -and $RestartAswarmPods) {
        Write-Host "`nRestarting $($stuckAswarmPods.Count) Pending A-SWARM pods..." -ForegroundColor Yellow
        $stuckAswarmPods | ForEach-Object {
            Write-Host "  - Deleting pod: $($_.metadata.name)" -ForegroundColor Gray
            kubectl delete pod $_.metadata.name -n aswarm --grace-period=0 --force 2>$null
        }
    } elseif ($stuckAswarmPods.Count -gt 0) {
        Write-Host "`nTip: Re-run with -RestartAswarmPods to evict Pending pods" -ForegroundColor DarkGray
    }
}

# Summary and recommendations
Write-Host "`n=== Recovery Complete ===" -ForegroundColor Green
Write-Host "Docker Desktop Kubernetes cluster has been recovered." -ForegroundColor Gray
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Re-apply your A-SWARM deployments if needed:"
Write-Host "   kubectl apply -f deploy/fastpath-with-identity.yaml" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Check logs for any pods still having issues:"
Write-Host "   kubectl logs -n aswarm [pod-name]" -ForegroundColor Gray
Write-Host ""
Write-Host "3. If issues persist, try these options:"
Write-Host "   - Run with -Deep for thorough Docker cleanup" -ForegroundColor Gray
Write-Host "   - Run with -VeryDeep for nuclear cleanup (backup first!)" -ForegroundColor Gray
Write-Host "   - Run with -Logs to capture detailed diagnostics" -ForegroundColor Gray
Write-Host ""

if ($Logs) {
    Write-Host "Diagnostic logs saved to:" -ForegroundColor Yellow
    $tempPath = $env:TEMP
    Write-Host "  $tempPath\ddesk-*.log" -ForegroundColor Gray
}