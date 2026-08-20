[CmdletBinding()]
param(
    [string]$Tag = 'retrieval-arena/verification:local',
    [string]$Receipt = '.tmp/verification-image.json',
    [ValidateSet('linux/amd64')][string]$Platform = 'linux/amd64'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$lock = Join-Path $repo 'containers\verification\requirements.lock'
$dockerfile = Join-Path $repo 'containers\verification\Dockerfile'
$commit = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'source-commit-unresolved' }
$tree = (& git -C $repo write-tree).Trim()
if ($LASTEXITCODE -ne 0 -or $tree -notmatch '^[0-9a-f]{40}$') { throw 'source-tree-unresolved' }
$lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $lock).Hash.ToLowerInvariant()

& docker build --platform $Platform --pull=false --file $dockerfile --tag $Tag `
    --build-arg "SOURCE_COMMIT=$commit" --build-arg "SOURCE_TREE=$tree" `
    --build-arg "DEPENDENCY_LOCK_SHA256=$lockHash" --build-arg "TARGET_OCI_PLATFORM=$Platform" $repo
if ($LASTEXITCODE -ne 0) { throw 'verification-image-build-failed' }
$imageId = (& docker image inspect --format '{{.Id}}' $Tag).Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') { throw 'verification-image-id-invalid' }

$receiptPath = if ([IO.Path]::IsPathRooted($Receipt)) {
    [IO.Path]::GetFullPath($Receipt)
} else {
    [IO.Path]::GetFullPath((Join-Path $repo $Receipt))
}
$receiptParent = Split-Path -Parent $receiptPath
New-Item -ItemType Directory -Force -Path $receiptParent | Out-Null
[ordered]@{
    schema_version = 'retrieval_arena.verification_image_receipt.v1'
    image = [ordered]@{ reference = $imageId; tag = $Tag }
    platform = $Platform
    source_commit = $commit
    source_tree = $tree
    dependency_lock_sha256 = $lockHash
} | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 -LiteralPath $receiptPath
Write-Output $receiptPath
