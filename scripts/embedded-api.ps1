[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
& python -B (Join-Path $PSScriptRoot 'embedded-api.py') @Arguments
exit $LASTEXITCODE
