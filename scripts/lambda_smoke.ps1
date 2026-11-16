$ErrorActionPreference = "Stop"

$image = "vfl-extract:local"
$containerId = $null

function Fail-Smoke([string]$Message) {
    Write-Error "Lambda image smoke failed: $Message"
    exit 1
}

try {
    # Build command matches the header comment in lambda/extract/Dockerfile.
    docker build --platform linux/amd64 -f lambda/extract/Dockerfile -t $image .
    if ($LASTEXITCODE -ne 0) { Fail-Smoke "docker build returned exit code $LASTEXITCODE" }

    docker run --rm --entrypoint python $image -c "import fitz, pyarrow, bs4, volab.text.extract, handler"
    if ($LASTEXITCODE -ne 0) { Fail-Smoke "image dependency/import check returned exit code $LASTEXITCODE" }

    $containerId = (docker run -d --rm -p 9000:8080 $image).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $containerId) { Fail-Smoke "could not start the Lambda container" }

    $event = '{"Records":[{"s3":{"bucket":{"name":"fixture"},"object":{"key":"clean/year=2020/doc_type=statement/ignored.parquet"}}}]}'
    $response = $null
    $deadline = (Get-Date).AddSeconds(60)
    do {
        try {
            $response = Invoke-RestMethod -Method Post `
                -Uri "http://127.0.0.1:9000/2015-03-31/functions/function/invocations" `
                -ContentType "application/json" -Body $event -TimeoutSec 5
            break
        } catch {
            if ((Get-Date) -ge $deadline) { Fail-Smoke "Lambda invocation endpoint did not become ready: $($_.Exception.Message)" }
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)

    if ($null -eq $response -or $response.processed -ne 0 -or @($response.errors).Count -ne 0) {
        Fail-Smoke "ignored-key invocation returned an unexpected response: $($response | ConvertTo-Json -Compress -Depth 5)"
    }
    Write-Output 'Lambda image smoke passed.'
} catch {
    Fail-Smoke $_.Exception.Message
} finally {
    if ($containerId) {
        docker stop $containerId | Out-Null
    }
}
