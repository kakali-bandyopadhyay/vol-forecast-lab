[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$sourceDir = Join-Path $repoRoot 'docs/diagrams'
$outputDir = Join-Path $repoRoot 'figures/diagrams'
$mermaidCliVersion = '11.4.2'

if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) {
    throw "Mermaid source directory not found: $sourceDir"
}

$sources = @(Get-ChildItem -LiteralPath $sourceDir -Filter '*.mmd' -File | Sort-Object Name)
if ($sources.Count -eq 0) {
    throw "No Mermaid source files found in $sourceDir"
}
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$versionOutput = & npx --yes --package "@mermaid-js/mermaid-cli@$mermaidCliVersion" mmdc --version
if ($LASTEXITCODE -ne 0 -or ($versionOutput -join "`n") -notmatch [regex]::Escape($mermaidCliVersion)) {
    throw "Could not verify @mermaid-js/mermaid-cli@$mermaidCliVersion with npx. Output: $($versionOutput -join ' ')"
}
Write-Output "Verified @mermaid-js/mermaid-cli version $mermaidCliVersion."

foreach ($source in $sources) {
    $baseName = [IO.Path]::GetFileNameWithoutExtension($source.Name)
    $svg = Join-Path $outputDir "$baseName.svg"
    $png = Join-Path $outputDir "$baseName.png"
    foreach ($export in @(@{ Path = $svg; Scale = $false }, @{ Path = $png; Scale = $true })) {
        if ($export.Scale) {
            & npx --yes --package "@mermaid-js/mermaid-cli@$mermaidCliVersion" mmdc -i $source.FullName -o $export.Path -s 2
        }
        else {
            & npx --yes --package "@mermaid-js/mermaid-cli@$mermaidCliVersion" mmdc -i $source.FullName -o $export.Path
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Mermaid export failed for '$($source.Name)' to '$($export.Path)' (exit $LASTEXITCODE). Verify npx can run @mermaid-js/mermaid-cli@$mermaidCliVersion."
        }
        if (-not (Test-Path -LiteralPath $export.Path -PathType Leaf) -or (Get-Item -LiteralPath $export.Path).Length -eq 0) {
            throw "Mermaid CLI returned success but produced no output: $($export.Path)"
        }
    }
}

Write-Output "Rendered $($sources.Count) Mermaid diagrams with @mermaid-js/mermaid-cli@$mermaidCliVersion."
