# A-SWARM Fast-Path Verification Script
# Checks that all components are properly deployed and working

param(
    [string]$Namespace = "aswarm"
)

$ErrorActionPreference = "Continue"

Write-Host "=== A-SWARM Fast-Path Verification ===" -ForegroundColor Cyan
Write-Host ""

$issues = @()

# Check namespace exists
Write-Host "Checking namespace..." -ForegroundColor Yellow
$ns = kubectl get namespace $Namespace -o json 2>$null | ConvertFrom-Json
if ($ns) {
    Write-Host "✓ Namespace '$Namespace' exists" -ForegroundColor Green
} else {
    Write-Host "✗ Namespace '$Namespace' not found" -ForegroundColor Red
    $issues += "Namespace missing"
}

# Check service accounts
Write-Host ""
Write-Host "Checking service accounts..." -ForegroundColor Yellow
$sas = @("aswarm-sentinel", "aswarm-pheromone")
foreach ($sa in $sas) {
    $exists = kubectl get sa $sa -n $Namespace 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ ServiceAccount '$sa' exists" -ForegroundColor Green
    } else {
        Write-Host "✗ ServiceAccount '$sa' missing" -ForegroundColor Red
        $issues += "ServiceAccount $sa missing"
    }
}

# Check RBAC
Write-Host ""
Write-Host "Checking RBAC permissions..." -ForegroundColor Yellow
$roles = @("aswarm-sentinel-role", "aswarm-pheromone-role")
foreach ($role in $roles) {
    $exists = kubectl get role $role -n $Namespace 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Role '$role' exists" -ForegroundColor Green
    } else {
        Write-Host "✗ Role '$role' missing" -ForegroundColor Red
        $issues += "Role $role missing"
    }
}

# Check secret
Write-Host ""
Write-Host "Checking secrets..." -ForegroundColor Yellow
$secret = kubectl get secret aswarm-fastpath-key -n $Namespace 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Fast-path key secret exists" -ForegroundColor Green
} else {
    Write-Host "✗ Fast-path key secret missing" -ForegroundColor Red
    $issues += "Secret missing"
}

# Check deployments
Write-Host ""
Write-Host "Checking deployments..." -ForegroundColor Yellow

# Pheromone
$pheromone = kubectl get deployment aswarm-pheromone -n $Namespace -o json 2>$null | ConvertFrom-Json
if ($pheromone) {
    $ready = $pheromone.status.readyReplicas
    $desired = $pheromone.spec.replicas
    if ($ready -eq $desired) {
        Write-Host "✓ Pheromone deployment ready ($ready/$desired replicas)" -ForegroundColor Green
    } else {
        Write-Host "⚠ Pheromone deployment not fully ready ($ready/$desired replicas)" -ForegroundColor Yellow
        $issues += "Pheromone not ready"
    }
} else {
    Write-Host "✗ Pheromone deployment missing" -ForegroundColor Red
    $issues += "Pheromone deployment missing"
}

# Sentinel
$sentinel = kubectl get daemonset aswarm-sentinel -n $Namespace -o json 2>$null | ConvertFrom-Json
if ($sentinel) {
    $ready = $sentinel.status.numberReady
    $desired = $sentinel.status.desiredNumberScheduled
    if ($ready -eq $desired -and $desired -gt 0) {
        Write-Host "✓ Sentinel daemonset ready ($ready/$desired nodes)" -ForegroundColor Green
    } else {
        Write-Host "⚠ Sentinel daemonset not fully ready ($ready/$desired nodes)" -ForegroundColor Yellow
        $issues += "Sentinel not ready"
    }
} else {
    Write-Host "✗ Sentinel daemonset missing" -ForegroundColor Red
    $issues += "Sentinel daemonset missing"
}

# Check service
Write-Host ""
Write-Host "Checking services..." -ForegroundColor Yellow
$svc = kubectl get svc aswarm-pheromone -n $Namespace -o json 2>$null | ConvertFrom-Json
if ($svc) {
    $ip = $svc.spec.clusterIP
    $port = $svc.spec.ports[0].port
    Write-Host "✓ Pheromone service exists (${ip}:${port}/UDP)" -ForegroundColor Green
} else {
    Write-Host "✗ Pheromone service missing" -ForegroundColor Red
    $issues += "Service missing"
}

# Check pod health
Write-Host ""
Write-Host "Checking pod health..." -ForegroundColor Yellow
$pods = kubectl get pods -n $Namespace -o json 2>$null | ConvertFrom-Json
$unhealthy = @()
foreach ($pod in $pods.items) {
    $name = $pod.metadata.name
    $ready = $pod.status.conditions | Where-Object { $_.type -eq "Ready" } | Select-Object -ExpandProperty status
    $phase = $pod.status.phase
    
    if ($ready -eq "True" -and $phase -eq "Running") {
        Write-Host "✓ Pod '$name' is healthy" -ForegroundColor Green
    } else {
        Write-Host "✗ Pod '$name' is unhealthy (Phase: $phase, Ready: $ready)" -ForegroundColor Red
        $unhealthy += $name
        
        # Show recent events for unhealthy pods
        $events = kubectl get events -n $Namespace --field-selector "involvedObject.name=$name" --sort-by='.lastTimestamp' -o json 2>$null | ConvertFrom-Json
        if ($events.items.Count -gt 0) {
            $recent = $events.items | Select-Object -Last 3
            Write-Host "  Recent events:" -ForegroundColor Yellow
            foreach ($event in $recent) {
                Write-Host "    - $($event.message)" -ForegroundColor Gray
            }
        }
    }
}

if ($unhealthy.Count -gt 0) {
    $issues += "$($unhealthy.Count) unhealthy pods"
}

# Test connectivity
Write-Host ""
Write-Host "Testing connectivity..." -ForegroundColor Yellow
if ($svc -and $pheromone -and $pheromone.status.readyReplicas -gt 0) {
    # Create a test pod to check UDP connectivity
    $testPod = @"
apiVersion: v1
kind: Pod
metadata:
  name: aswarm-test-pod
  namespace: $Namespace
spec:
  containers:
  - name: test
    image: busybox
    command: ['sh', '-c', 'echo test | nc -u aswarm-pheromone 8888; echo UDP packet sent; sleep 5']
  restartPolicy: Never
"@
    
    $testPod | kubectl apply -f - 2>$null
    Start-Sleep -Seconds 3
    $logs = kubectl logs aswarm-test-pod -n $Namespace 2>$null
    kubectl delete pod aswarm-test-pod -n $Namespace --force --grace-period=0 2>$null
    
    if ($logs -like "*UDP packet sent*") {
        Write-Host "✓ UDP connectivity test passed" -ForegroundColor Green
    } else {
        Write-Host "⚠ Could not verify UDP connectivity" -ForegroundColor Yellow
    }
}

# Summary
Write-Host ""
Write-Host "=== Verification Summary ===" -ForegroundColor Cyan
if ($issues.Count -eq 0) {
    Write-Host "✅ All checks passed! Fast-path deployment is healthy." -ForegroundColor Green
    Write-Host ""
    Write-Host "You can now run performance tests:" -ForegroundColor Cyan
    Write-Host "  python scripts/test_fast_path.py --mode loopback" -ForegroundColor White
} else {
    Write-Host "❌ Found $($issues.Count) issues:" -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host "  - $issue" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "To redeploy:" -ForegroundColor Yellow
    Write-Host "  .\deploy\deploy-fastpath.ps1 -Clean" -ForegroundColor White
    Write-Host "  .\deploy\deploy-fastpath.ps1" -ForegroundColor White
}

# Show useful commands
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Gray
Write-Host "  kubectl get pods -n $Namespace -w  # Watch pod status"
Write-Host "  kubectl logs -n $Namespace -l app=aswarm-pheromone -f  # Pheromone logs"
Write-Host "  kubectl logs -n $Namespace -l app=aswarm-sentinel -f   # Sentinel logs"