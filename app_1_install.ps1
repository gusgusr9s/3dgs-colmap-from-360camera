param(
    [ValidateSet('auto','cpu','cuda')][string]$Device = 'auto',
    [string]$VenvName = '.venv-win',
    [switch]$Check,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
# This is the only Windows installation entry point. Helpers live under scripts/install.
& (Join-Path $PSScriptRoot 'scripts\install\windows.ps1') @PSBoundParameters
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
