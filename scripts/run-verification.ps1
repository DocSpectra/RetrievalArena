[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('focused','all')][string]$Command,
    [string]$Receipt = '.tmp/verification-image.json'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$receiptPath = if ([IO.Path]::IsPathRooted($Receipt)) {
    [IO.Path]::GetFullPath($Receipt)
} else {
    [IO.Path]::GetFullPath((Join-Path $repo $Receipt))
}
$value = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
if ($value.schema_version -ne 'retrieval_arena.verification_image_receipt.v1') { throw 'verification-receipt-schema-invalid' }
if ($value.platform -ne 'linux/amd64') { throw 'verification-platform-unregistered' }
$image = [string]$value.image.reference
if ($image -notmatch '^(?:[^\s@]+@)?sha256:[0-9a-f]{64}$') { throw 'verification-image-reference-mutable' }

$arguments = @(
    'run','--rm','--platform',[string]$value.platform,'--network','none','--read-only',
    '--security-opt','no-new-privileges','--cap-drop','ALL','--pids-limit','512',
    '--mount',"type=bind,src=$repo,dst=/workspace/source,readonly",
    '--tmpfs','/workspace/source/.tmp_unit_tests:rw,nosuid,nodev,size=512m,uid=65532,gid=65532',
    '--tmpfs','/workspace/source/unit_tests/_tmp_html_report:rw,nosuid,nodev,size=256m,uid=65532,gid=65532',
    '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532',
    '--env','HOME=/tmp','--env','LANG=C.UTF-8','--env','LC_ALL=C.UTF-8',
    '--env','PATH=/usr/local/bin:/usr/bin:/bin','--env','PYTHONDONTWRITEBYTECODE=1',
    '--env','PYTHONHASHSEED=0','--env','PYTHONIOENCODING=utf-8',
    '--env','TMPDIR=/workspace/source/.tmp_unit_tests','--env','TZ=UTC',
    $image,$Command
)
& docker @arguments
exit $LASTEXITCODE
