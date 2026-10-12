#requires -Version 7.0
[CmdletBinding(PositionalBinding = $false)]
param([string]$Tag = 'retrieval-arena/ragas:local', [string]$Receipt = '.tmp/ragas-image.json')
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$commit = (& git -C $repo rev-parse HEAD).Trim()
$tree = (& git -C $repo rev-parse 'HEAD^{tree}').Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[a-f0-9]{40}$' -or $tree -notmatch '^[a-f0-9]{40}$') { throw 'ragas-source-identity' }
# Build only a committed snapshot. Existing unrelated working-tree notes are neither
# modified nor copied into the image; the image never mounts the working tree.
$owned = @('retrieval_arena', 'containers/ragas', 'unit_tests/ragas_fixture.py', 'unit_tests/test_ragas_scoring.py', 'unit_tests/test_ragas_api.py', 'unit_tests/test_model_providers.py', 'scripts/build-ragas.ps1', 'scripts/run-ragas.ps1')
$dirty = & git -C $repo status --porcelain -- @owned
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'ragas-implementation-not-committed' }
$build = Join-Path $repo ('.tmp/ragas-build-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $build | Out-Null
$archive = Join-Path $build 'source.tar'
& git -C $repo archive --format=tar "--output=$archive" $commit -- @owned
if ($LASTEXITCODE -ne 0) { throw 'ragas-source-export' }
& tar -xf $archive -C $build
if ($LASTEXITCODE -ne 0) { throw 'ragas-source-unpack' }
$lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $build 'containers/ragas/requirements.lock')).Hash.ToLowerInvariant()
& docker build --platform linux/amd64 --file (Join-Path $build 'containers/ragas/Dockerfile') --tag $Tag `
    --build-arg "SOURCE_COMMIT=$commit" --build-arg "SOURCE_TREE=$tree" --build-arg "DEPENDENCY_LOCK_SHA256=$lockHash" $build
if ($LASTEXITCODE -ne 0) { throw 'ragas-image-build' }
$image = (& docker image inspect --format '{{.Id}}' $Tag).Trim()
if ($LASTEXITCODE -ne 0 -or $image -notmatch '^sha256:[a-f0-9]{64}$') { throw 'ragas-image-id' }
$adapters = [ordered]@{}
foreach ($name in @('build-ragas.ps1', 'run-ragas.ps1')) {
    $adapters[$name] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot $name)).Hash.ToLowerInvariant()
}
$value = [ordered]@{schema_version='retrieval_arena.ragas_image_receipt.v1'; platform='linux/amd64'; source_commit=$commit; source_tree=$tree; dependency_lock_sha256=$lockHash; image=$image; adapters=$adapters; sha256=('0' * 64)}
$raw = ($value | ConvertTo-Json -Depth 10 -Compress) + "`n"
$hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($raw))).ToLowerInvariant()
$path = if ([IO.Path]::IsPathRooted($Receipt)) { $Receipt } else { Join-Path $repo $Receipt }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
[IO.File]::WriteAllText($path, $raw.Replace(('0' * 64), $hash), [Text.UTF8Encoding]::new($false))
Write-Output $path
