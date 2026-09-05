[CmdletBinding()]
param(
    [string]$Tag = 'retrieval-arena/verification:local',
    [string]$Receipt = '.tmp/verification-image.json',
    [ValidateSet('linux/amd64')][string]$Platform = 'linux/amd64',
    [string]$ProvisionedImage
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$porcelain = (& git -C $repo status --porcelain)
if ($LASTEXITCODE -ne 0 -or $porcelain) { throw 'verification-source-not-clean' }
$null = & git -C $repo diff --quiet
if ($LASTEXITCODE -ne 0) { throw 'verification-source-worktree-dirty' }
$null = & git -C $repo diff --cached --quiet
if ($LASTEXITCODE -ne 0) { throw 'verification-source-index-dirty' }
$lock = Join-Path $repo 'containers\verification\requirements.lock'
$dockerfile = Join-Path $repo 'containers\verification\Dockerfile'
$commit = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'source-commit-unresolved' }
$tree = (& git -C $repo write-tree).Trim()
if ($LASTEXITCODE -ne 0 -or $tree -notmatch '^[0-9a-f]{40}$') { throw 'source-tree-unresolved' }
$lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $lock).Hash.ToLowerInvariant()

$provisionedArguments = @()
if ($ProvisionedImage) {
    if ($ProvisionedImage -notmatch '^sha256:[0-9a-f]{64}$') { throw 'verification-provisioned-image-mutable' }
    $existing = & docker image inspect $ProvisionedImage | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $existing.Id -ne $ProvisionedImage -or $existing.Os -ne 'linux' -or $existing.Architecture -ne 'amd64' -or $existing.Config.Labels.'io.retrieval-arena.verification.dependency-lock-sha256' -ne $lockHash) { throw 'verification-provisioned-image-mismatch' }
    $localTag = 'retrieval-arena/provisioned:' + $ProvisionedImage.Substring(7)
    & docker tag $ProvisionedImage $localTag
    if ($LASTEXITCODE -ne 0) { throw 'verification-provisioned-tag-failed' }
    $provisionedArguments = @('--build-arg', "BASE_IMAGE=$localTag", '--build-arg', 'PROVISIONED=true')
}
& docker build --network=none --platform $Platform --pull=false --file $dockerfile --tag $Tag @provisionedArguments `
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
$zero = '0' * 64
$text = "{`"dependency_lock_sha256`":`"$lockHash`",`"image`":{`"reference`":`"$imageId`",`"tag`":`"$Tag`"},`"platform`":`"$Platform`",`"receipt_sha256`":`"$zero`",`"schema_version`":`"retrieval_arena.verification_image_receipt.v2`",`"source_commit`":`"$commit`",`"source_tree`":`"$tree`"}`n"
$encoding = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($receiptPath, $text, $encoding)
$receiptHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $receiptPath).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($receiptPath, $text.Replace($zero, $receiptHash), $encoding)
Write-Output $receiptPath
