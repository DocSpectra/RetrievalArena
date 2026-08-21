[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('focused','all','evaluate-profile','review-packet')][string]$Command,
    [string]$InputRoot,
    [string]$OutputRoot,
    [string]$Receipt = '.tmp/verification-image.json'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$porcelain = (& git -C $repo status --porcelain)
if ($LASTEXITCODE -ne 0 -or $porcelain) { throw 'verification-source-not-clean' }
$receiptPath = if ([IO.Path]::IsPathRooted($Receipt)) {
    [IO.Path]::GetFullPath($Receipt)
} else {
    [IO.Path]::GetFullPath((Join-Path $repo $Receipt))
}
$value = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
if ($value.schema_version -ne 'retrieval_arena.verification_image_receipt.v2') { throw 'verification-receipt-schema-invalid' }
if ($value.platform -ne 'linux/amd64') { throw 'verification-platform-unregistered' }
$image = [string]$value.image.reference
if ($image -notmatch '^(?:[^\s@]+@)?sha256:[0-9a-f]{64}$') { throw 'verification-image-reference-mutable' }
$rawReceipt = Get-Content -Raw -LiteralPath $receiptPath
$zeroReceipt = $rawReceipt -replace '"receipt_sha256":"[0-9a-f]{64}"', ('"receipt_sha256":"' + ('0' * 64) + '"')
$actualReceiptHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.UTF8Encoding]::new($false).GetBytes($zeroReceipt))).ToLowerInvariant()
if ($value.receipt_sha256 -ne $actualReceiptHash) { throw 'verification-receipt-self-hash-invalid' }
$expectedLock = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $repo 'containers\verification\requirements.lock')).Hash.ToLowerInvariant()
if ($value.source_commit -notmatch '^[0-9a-f]{40}$' -or $value.source_tree -notmatch '^[0-9a-f]{40}$' -or $value.dependency_lock_sha256 -ne $expectedLock) { throw 'verification-receipt-identity-invalid' }
$null = & git -C $repo diff --quiet
if ($LASTEXITCODE -ne 0) { throw 'verification-source-worktree-dirty' }
$null = & git -C $repo diff --cached --quiet
if ($LASTEXITCODE -ne 0) { throw 'verification-source-index-dirty' }
$head = (& git -C $repo rev-parse HEAD).Trim(); $tree = (& git -C $repo write-tree).Trim()
if ($head -ne $value.source_commit -or $tree -ne $value.source_tree) { throw 'verification-mounted-source-identity-mismatch' }
$labels = (& docker image inspect --format '{{json .Config.Labels}}' $image | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $labels.'org.opencontainers.image.revision' -ne $value.source_commit -or $labels.'io.retrieval-arena.source.tree' -ne $value.source_tree -or $labels.'io.retrieval-arena.verification.dependency-lock-sha256' -ne $value.dependency_lock_sha256 -or $labels.'io.retrieval-arena.verification.platform' -ne $value.platform) { throw 'verification-image-label-mismatch' }

if ($Command -in @('evaluate-profile','review-packet')) {
    if ([string]::IsNullOrWhiteSpace($InputRoot) -or [string]::IsNullOrWhiteSpace($OutputRoot)) { throw 'verification-operator-input-output-required' }
    $inputPath = (Resolve-Path -LiteralPath $InputRoot).Path
    $outputPath = (Resolve-Path -LiteralPath $OutputRoot).Path
    $roots = @($repo,$inputPath,$outputPath)
    $normalizedRoots = $roots | ForEach-Object { $_.TrimEnd([char[]]@('\','/')).ToLowerInvariant() }
    $nestedRoots = $false
    foreach ($left in $normalizedRoots) { foreach ($right in $normalizedRoots) { if ($left -ne $right -and $right.StartsWith($left + '\')) { $nestedRoots = $true } } }
    if (($roots | Select-Object -Unique).Count -ne 3 -or $nestedRoots -or -not (Test-Path -LiteralPath (Join-Path $inputPath 'operator-request.json') -PathType Leaf)) { throw 'verification-operator-root-admission-invalid' }
    if ((Get-ChildItem -Force -LiteralPath $outputPath | Select-Object -ExpandProperty Name) -ne '.retrieval-arena-output-owner') { throw 'verification-operator-output-not-fresh' }
    $arguments = @(
        'run','--rm','--platform',[string]$value.platform,'--network','none','--read-only',
        '--security-opt','no-new-privileges','--cap-drop','ALL','--pids-limit','512','--memory','2g','--cpus','2',
        '--mount',"type=bind,src=$repo,dst=/workspace/source,readonly",
        '--mount',"type=bind,src=$inputPath,dst=/workspace/input,readonly",
        '--mount',"type=bind,src=$outputPath,dst=/workspace/output",
        '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532',
        '--env',"RA_SOURCE_COMMIT=$($value.source_commit)",'--env',"RA_SOURCE_TREE=$($value.source_tree)",
        '--env',"RA_DEPENDENCY_LOCK_SHA256=$($value.dependency_lock_sha256)",'--env',"RA_IMAGE_REFERENCE=$image",'--env',"RA_PLATFORM=$($value.platform)",
        '--env','HOME=/tmp','--env','TMPDIR=/tmp','--env','PYTHONHASHSEED=0','--env','PYTHONIOENCODING=utf-8','--env','TZ=UTC',
        $image,$Command
    )
    & docker @arguments
    exit $LASTEXITCODE
}

$arguments = @(
    'run','--rm','--platform',[string]$value.platform,'--network','none','--read-only',
    '--security-opt','no-new-privileges','--cap-drop','ALL','--pids-limit','512','--memory','2g','--cpus','2',
    '--mount',"type=bind,src=$repo,dst=/workspace/source,readonly",
    '--tmpfs','/workspace/source/.tmp_unit_tests:rw,nosuid,nodev,size=512m,uid=65532,gid=65532',
    '--tmpfs','/workspace/source/unit_tests/_tmp_html_report:rw,nosuid,nodev,size=256m,uid=65532,gid=65532',
    '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532',
    '--env','HOME=/tmp','--env','LANG=C.UTF-8','--env','LC_ALL=C.UTF-8',
    '--env','PATH=/usr/local/bin:/usr/bin:/bin','--env','PYTHONDONTWRITEBYTECODE=1',
    '--env','PYTHONHASHSEED=0','--env','PYTHONIOENCODING=utf-8',
    '--env','TMPDIR=/workspace/source/.tmp_unit_tests','--env','TZ=UTC',
    '--env',"RA_SOURCE_COMMIT=$($value.source_commit)",'--env',"RA_SOURCE_TREE=$($value.source_tree)",
    '--env',"RA_DEPENDENCY_LOCK_SHA256=$($value.dependency_lock_sha256)",'--env',"RA_IMAGE_REFERENCE=$image",
    $image,$Command
)
& docker @arguments
exit $LASTEXITCODE
