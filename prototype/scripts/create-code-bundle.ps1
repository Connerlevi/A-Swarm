# Create Code Bundle for A-SWARM Production Deployment
# Packages sentinel/ and pheromone/ into a Kubernetes Secret

param(
    [string]$OutputPath = "aswarm-code-bundle.yaml",
    [string]$Namespace = "aswarm"
)

$ErrorActionPreference = "Stop"

Write-Host "Creating A-SWARM code bundle..." -ForegroundColor Cyan

# Check required directories
if (-not (Test-Path "sentinel") -or -not (Test-Path "pheromone")) {
    throw "sentinel/ and pheromone/ directories not found. Run from prototype directory."
}

# Create tarball
$tarFile = "aswarm-code.tar.gz"
if (Test-Path $tarFile) { Remove-Item $tarFile -Force }

if (Get-Command tar.exe -ErrorAction SilentlyContinue) {
    & tar.exe -czf $tarFile sentinel pheromone
} else {
    Write-Host "tar.exe not found, using WSL..." -ForegroundColor Yellow
    wsl tar czf $tarFile sentinel pheromone
}

if ($LASTEXITCODE -ne 0) { throw "tar failed" }

# Check size
$size = (Get-Item $tarFile).Length
if ($size -gt 1000000) {
    throw "Code bundle is $size bytes (>1MiB). Too large for Kubernetes Secret."
}

$hash = (Get-FileHash $tarFile -Algorithm SHA256).Hash
Write-Host "Bundle: $([math]::Round($size/1024))KB, SHA256: $($hash.Substring(0,16))..." -ForegroundColor Gray

# Base64 encode
$codeBundle = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($tarFile))

# Create YAML
$yaml = @"
apiVersion: v1
kind: Secret
metadata:
  name: aswarm-code-bundle
  namespace: $Namespace
  annotations:
    aswarm.ai/bundle-sha: $hash
    aswarm.ai/created: $(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')
type: Opaque
data:
  code.tar.gz: $codeBundle
"@

$yaml | Out-File -FilePath $OutputPath -Encoding UTF8
Remove-Item $tarFile

Write-Host "[OK] Code bundle saved to $OutputPath" -ForegroundColor Green
Write-Host ""
Write-Host "To apply:" -ForegroundColor Cyan
Write-Host "kubectl apply -f $OutputPath" -ForegroundColor White