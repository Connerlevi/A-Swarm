param(
    [string]$Selector = "app=anomaly",
    [string]$Namespace = "aswarm"
)

Write-Host "[A-SWARM] Applying quarantine label and NetworkPolicy..." -ForegroundColor Cyan
kubectl -n $Namespace label pods -l $Selector aswarm/quarantine=true --overwrite | Out-Null
kubectl apply -f k8s/quarantine-template.yaml | Out-Null
Write-Host "[A-SWARM] Quarantine applied." -ForegroundColor Green