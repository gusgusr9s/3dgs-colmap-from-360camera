param(
    [int]$Port = 8000,
    [string]$VenvName = '.venv-win',
    [ValidateSet('full','balanced')][string]$CpuMode,
    [ValidateRange(1,4096)][int]$CpuThreads
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot "$VenvName\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found at $python. Run .\app_1_install.ps1 first."
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$registeredPath = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$env:PATH = (Join-Path $PSScriptRoot "$VenvName\Scripts") + ';' + (Join-Path $PSScriptRoot 'tools\colmap\bin') + ';' + (Join-Path $PSScriptRoot 'tools\brush') + ';' + $registeredPath + ';' + $env:PATH
$webArgs = @('--port', $Port)
if ($PSBoundParameters.ContainsKey('CpuMode')) { $webArgs += @('--cpu-mode', $CpuMode) }
if ($PSBoundParameters.ContainsKey('CpuThreads')) { $webArgs += @('--cpu-threads', $CpuThreads) }
& $python (Join-Path $PSScriptRoot 'web_app.py') @webArgs
exit $LASTEXITCODE
