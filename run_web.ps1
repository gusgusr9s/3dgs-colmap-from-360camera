param(
    [int]$Port = 8000,
    [string]$VenvName = '.venv-win'
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot "$VenvName\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found at $python. Run .\setup_windows.ps1 first."
}

& $python (Join-Path $PSScriptRoot 'web_app.py') --port $Port
