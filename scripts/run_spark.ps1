<#
.SYNOPSIS
    Run a repository PySpark script in the official Apache Spark container.
.EXAMPLE
    .\scripts\run_spark.ps1 spark\compact_stats.py --input /opt/repo/data/clean
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ScriptPath,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SparkArgs
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$candidateScript = if ([System.IO.Path]::IsPathRooted($ScriptPath)) { $ScriptPath } else { Join-Path $repoRoot $ScriptPath }
$resolvedScript = (Resolve-Path -LiteralPath $candidateScript).Path
$rootUri = [Uri]::new($repoRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar)
$scriptUri = [Uri]::new($resolvedScript)
$relativeScript = [Uri]::UnescapeDataString($rootUri.MakeRelativeUri($scriptUri).ToString()).Replace("/", [System.IO.Path]::DirectorySeparatorChar)
if ([System.IO.Path]::IsPathRooted($relativeScript) -or $relativeScript.StartsWith(".." + [System.IO.Path]::DirectorySeparatorChar) -or $relativeScript -eq "..") {
    throw "The PySpark script must be inside the repository."
}

$containerScript = "/opt/repo/" + ($relativeScript -replace "\\", "/")
$dataPath = Join-Path $repoRoot "data"
docker run --rm `
    --volume "${repoRoot}:/opt/repo:ro" `
    --volume "${dataPath}:/opt/repo/data:rw" `
    apache/spark:4.3.0-python3 `
    /opt/spark/bin/spark-submit $containerScript @SparkArgs
exit $LASTEXITCODE
