param(
    [string]$VenvName = '.venv-win'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venvPython = Join-Path $root "$VenvName\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw 'Python was not found. Install Python 3.12 from python.org, enable Add Python to PATH, then reopen PowerShell.'
    }
    & $python.Source -m venv (Join-Path $root $VenvName)
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}

Write-Host "Installing Python dependencies into $venvPython ..."
& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip.' }
& $venvPython -m pip install -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
& $venvPython -m pip install -r (Join-Path $root 'requirements-gpu.txt')
if ($LASTEXITCODE -ne 0) { throw 'GPU preparation dependency installation failed.' }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Incompatible Python dependencies.' }

& $venvPython (Join-Path $root 'scripts\install_viewer_assets.py')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the local model viewer assets.' }

Write-Host 'Caching the person-segmentation weights used to remove camera operators...'
& $venvPython -c "from torchvision.models.segmentation import lraspp_mobilenet_v3_large, LRASPP_MobileNet_V3_Large_Weights; lraspp_mobilenet_v3_large(weights=LRASPP_MobileNet_V3_Large_Weights.DEFAULT)"
if ($LASTEXITCODE -ne 0) { throw 'Could not download the person-segmentation weights.' }

& (Join-Path $root 'install_colmap.ps1')
if ($LASTEXITCODE -ne 0) { throw 'COLMAP installation failed.' }

& (Join-Path $root 'install_brush.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Brush installation failed.' }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'FFmpeg is required by Nerfstudio. Install it with winget install Gyan.FFmpeg.Essentials, then reopen PowerShell.' }
    & $winget.Source install --id Gyan.FFmpeg.Essentials --exact --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw 'FFmpeg installation failed.' }
}

Write-Host ''
Write-Host 'Setup complete. Start the app with:'
Write-Host '  .\run_web.ps1'
