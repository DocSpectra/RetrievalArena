#requires -Version 7.0
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)][ValidateSet('replay','judge','probe')][string]$Command,
    # Not used by probe (config only).
    [string]$Batch,
    [string]$Rubrics,
    [Parameter(Mandatory = $true)][string]$Artifact,
    [Parameter(Mandatory = $true)][string]$InputRoot,
    [string]$CacheRoot,
    [switch]$NoCache
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $InputRoot) { throw 'ragas-input-root-must-be-new' }
if (($Command -eq 'probe') -ne (-not $Batch -and -not $Rubrics)) { throw 'ragas-probe-takes-config-only' }
$artifactPath = (Resolve-Path -LiteralPath $Artifact).Path
New-Item -ItemType Directory -Path $InputRoot | Out-Null
if ($Command -ne 'probe') {
    Copy-Item -LiteralPath (Resolve-Path -LiteralPath $Batch).Path -Destination (Join-Path $InputRoot 'batch.json')
    Copy-Item -LiteralPath (Resolve-Path -LiteralPath $Rubrics).Path -Destination (Join-Path $InputRoot 'rubrics.json')
}
$artifactName = if ($Command -eq 'replay') { 'scores.json' } else { 'config.json' }
Copy-Item -LiteralPath $artifactPath -Destination (Join-Path $InputRoot $artifactName)
if ($CacheRoot) {
    if ($Command -ne 'judge') { throw 'ragas-cache-only-for-judge' }
    $cache = Join-Path $InputRoot 'cache'
    New-Item -ItemType Directory -Path $cache | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $CacheRoot -File) {
        if ($file.Name -notmatch '^[a-f0-9]{64}\.json$') { throw 'ragas-cache-filename' }
        Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $cache $file.Name)
    }
}
$root = (Resolve-Path -LiteralPath $InputRoot).Path
$inventory = [ordered]@{}
foreach ($file in Get-ChildItem -LiteralPath $root -File -Recurse | Sort-Object FullName) {
    $relative = [IO.Path]::GetRelativePath($root, $file.FullName).Replace('\','/')
    $inventory[$relative] = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
}
$request = [ordered]@{schema_version='retrieval_arena.ragas_operator_request.v1'; command=$Command; input_inventory=$inventory; use_cache=(-not $NoCache.IsPresent)}
[IO.File]::WriteAllText((Join-Path $root 'operator-request.json'), ($request | ConvertTo-Json -Depth 10 -Compress) + "`n", [Text.UTF8Encoding]::new($false))
Write-Output $root
