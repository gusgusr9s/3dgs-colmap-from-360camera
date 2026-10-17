param(
    [int]$Port = 8000,
    [string]$VenvName = '.venv-win',
    [ValidateSet('full','balanced')][string]$CpuMode,
    [ValidateRange(1,4096)][int]$CpuThreads
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot "$VenvName\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found at $python. Run .\setup_windows.ps1 first."
}

$webArgs = @('--port', $Port)
if ($PSBoundParameters.ContainsKey('CpuMode')) { $webArgs += @('--cpu-mode', $CpuMode) }
if ($PSBoundParameters.ContainsKey('CpuThreads')) { $webArgs += @('--cpu-threads', $CpuThreads) }
& $python (Join-Path $PSScriptRoot 'web_app.py') @webArgs
