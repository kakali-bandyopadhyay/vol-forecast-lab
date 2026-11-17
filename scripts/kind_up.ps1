$ErrorActionPreference = "Stop"

$missing = @()
foreach ($tool in @("docker", "kind", "kubectl")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        $missing += $tool
    }
}

if ($missing.Count -gt 0) {
    foreach ($tool in $missing) {
        switch ($tool) {
            "docker" { Write-Output "docker is required. Install Docker Desktop for Windows and start its engine." }
            "kind" { Write-Output "kind v0.33.0 is required. Install it from https://kind.sigs.k8s.io/docs/user/quick-start/#installation." }
            "kubectl" { Write-Output "kubectl is required. Install it from https://kubernetes.io/docs/tasks/tools/install-kubectl-windows/." }
        }
    }
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$kindConfig = Join-Path $repoRoot "k8s/kind-config.yaml"
$clusterNames = @(kind get clusters)
if ($clusterNames -notcontains "vfl") {
    kind create cluster --config $kindConfig
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

docker build --tag vol-forecast-lab:local $repoRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

kind load docker-image vol-forecast-lab:local --name vfl
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$manifests = @(Get-ChildItem -Path (Join-Path $repoRoot "k8s") -Filter "*.yaml" -File |
    Where-Object { $_.Name -ne "kind-config.yaml" })

if ($manifests.Count -gt 0) {
    foreach ($manifest in $manifests) {
        kubectl apply -f $manifest.FullName
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    foreach ($deployment in @(kubectl get deploy -o name)) {
        kubectl rollout status $deployment --timeout=120s
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $health = curl.exe --fail --show-error --silent http://127.0.0.1:30080/health
    $curlExitCode = $LASTEXITCODE
    Write-Output $health
    if ($curlExitCode -ne 0) { exit $curlExitCode }
} else {
    Write-Output "no manifests yet"
}
