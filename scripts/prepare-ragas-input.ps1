#requires -Version 7.0
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)][ValidateSet('replay','judge')][string]$Command,
    [Parameter(Mandatory = $true)][string]$Batch,
    [Parameter(Mandatory = $true)][string]$Rubrics,
    [Parameter(Mandatory = $true)][string]$Artifact,
    [Parameter(Mandatory = $true)][string]$InputRoot,
    [string]$CacheRoot,
    [switch]$NoCache
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $InputRoot) { throw 'ragas-input-root-must-be-new' }
$sources = @((Resolve-Path -LiteralPath $Batch).Path, (Resolve-Path -LiteralPath $Rubrics).Path, (Resolve-Path -LiteralPath $Artifact).Path)
New-Item -ItemType Directory -Path $InputRoot | Out-Null
Copy-Item -LiteralPath $sources[0] -Destination (Join-Path $InputRoot 'batch.json')
Copy-Item -LiteralPath $sources[1] -Destination (Join-Path $InputRoot 'rubrics.json')
$artifactName = if ($Command -eq 'replay') { 'scores.json' } else { 'config.json' }
Copy-Item -LiteralPath $sources[2] -Destination (Join-Path $InputRoot $artifactName)
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
