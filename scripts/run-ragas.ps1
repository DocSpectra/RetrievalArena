#requires -Version 7.0
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)][ValidateSet('smoke','fixture','replay','judge','probe')][string]$Command,
    [string]$InputRoot,
    [string]$OutputRoot,
    # judge/probe only. external_api: the operator admits one provider host (RA-D07);
    # the credential is passed by name, its value never on a command line.
    [ValidateSet('host_model_alias','external_api')][string]$Network = 'host_model_alias',
    [ValidateSet('ANTHROPIC_API_KEY','OPENAI_API_KEY')][string]$CredentialEnv,
    [string]$Receipt = '.tmp/ragas-image.json'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$receiptPath = if ([IO.Path]::IsPathRooted($Receipt)) { $Receipt } else { Join-Path $repo $Receipt }
$raw = Get-Content -Raw -LiteralPath $receiptPath
$value = $raw | ConvertFrom-Json
if ($value.schema_version -ne 'retrieval_arena.ragas_image_receipt.v1' -or $value.platform -ne 'linux/amd64' -or $value.image -notmatch '^sha256:[a-f0-9]{64}$') { throw 'ragas-receipt-schema' }
$zero = $raw -replace '"sha256":"[a-f0-9]{64}"', ('"sha256":"' + ('0' * 64) + '"')
$hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($zero))).ToLowerInvariant()
if ($hash -ne $value.sha256) { throw 'ragas-receipt-hash' }
foreach ($name in @('build-ragas.ps1', 'run-ragas.ps1')) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot $name)).Hash.ToLowerInvariant()
    if ($actual -ne $value.adapters.$name) { throw 'ragas-adapter-identity' }
}
$head = (& git -C $repo rev-parse HEAD).Trim()
$tree = (& git -C $repo rev-parse 'HEAD^{tree}').Trim()
if ($head -ne $value.source_commit -or $tree -ne $value.source_tree) { throw 'ragas-source-identity' }
$info = (& docker image inspect $value.image | ConvertFrom-Json)[0]
if ($LASTEXITCODE -ne 0 -or $info.Id -ne $value.image -or $info.Os -ne 'linux' -or $info.Architecture -ne 'amd64') { throw 'ragas-image-platform' }
$labels = $info.Config.Labels
if ($labels.'org.opencontainers.image.revision' -ne $value.source_commit -or $labels.'io.retrieval-arena.source.tree' -ne $value.source_tree -or $labels.'io.retrieval-arena.ragas.dependency-lock-sha256' -ne $value.dependency_lock_sha256 -or $labels.'io.retrieval-arena.ragas.contract' -ne 'retrieval_arena.ragas_image.v1') { throw 'ragas-image-labels' }
$network = if ($Command -in @('judge','probe')) { $Network } else { 'none' }
$networkArgs = switch ($network) {
    'host_model_alias' { @('--network','bridge','--add-host','host.docker.internal:host-gateway') }
    'external_api' { if (-not $CredentialEnv) { throw 'ragas-credential-env-required' }; @('--network','bridge','--env',$CredentialEnv) }
    default { @('--network','none') }
}
$mounts = @()
if ($Command -ne 'smoke') {
    $out = (Resolve-Path -LiteralPath $OutputRoot).Path
    if ((Get-ChildItem -Force -LiteralPath $out).Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $out '.retrieval-arena-output-owner') -PathType Leaf)) { throw 'ragas-output-not-fresh' }
    $mounts += @('--mount', "type=bind,src=$out,dst=/output")
    if ($Command -in @('replay','judge','probe')) {
        $inputPath = (Resolve-Path -LiteralPath $InputRoot).Path
        if ($out -eq $inputPath -or $out.StartsWith($inputPath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or $inputPath.StartsWith($out.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'ragas-input-output-overlap' }
        $mounts += @('--mount', "type=bind,src=$inputPath,dst=/input,readonly")
    }
}
& docker run --rm --platform linux/amd64 @networkArgs --read-only --user 65532:65532 `
    --security-opt no-new-privileges --cap-drop ALL --pids-limit 256 --memory 4g --cpus 2 `
    --tmpfs '/tmp:rw,nosuid,nodev,size=256m,uid=65532,gid=65532' @mounts `
    --env "RA_RAGAS_NETWORK=$network" --env "RA_IMAGE_REFERENCE=$($value.image)" `
    --env "RA_SOURCE_COMMIT=$($value.source_commit)" --env "RA_SOURCE_TREE=$($value.source_tree)" $value.image $Command
exit $LASTEXITCODE
