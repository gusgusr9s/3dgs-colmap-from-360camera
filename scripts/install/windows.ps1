param(
    [ValidateSet('auto','cpu','cuda')][string]$Device = 'auto',
    [string]$VenvName = '.venv-win',
    [switch]$Check,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$venv = Join-Path $root $VenvName
$python = Join-Path $venv 'Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    # Windows PowerShell 5.1 can turn harmless native stderr into terminating errors.
    $savedPreference = $ErrorActionPreference
    try { $ErrorActionPreference = 'Continue'; & $File @Arguments }
    finally { $ErrorActionPreference = $savedPreference }
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $File $($Arguments -join ' ')" }
}
function Refresh-Path {
    $env:PATH = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User') + ';' + $env:PATH
    $env:PATH = (Join-Path $venv 'Scripts') + ';' + (Join-Path $root 'tools\colmap\bin') + ';' + (Join-Path $root 'tools\brush') + ';' + $env:PATH
}
function Find-Python312 {
    $candidates = @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'), (Join-Path $env:ProgramFiles 'Python312\python.exe'))
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $found = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $candidates += $found }
        } catch { Write-Verbose 'Python launcher has no usable Python 3.12 yet.' }
    }
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found -and $found.Source -notlike '*WindowsApps*') { $candidates += $found.Source }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $version = & $candidate -c 'import sys,struct; print(sys.version_info.major, sys.version_info.minor, struct.calcsize(chr(80))*8)'
            if ($LASTEXITCODE -eq 0 -and $version -eq '3 12 64') { return $candidate }
        }
    }
    return $null
}
function Install-Winget([string]$Id, [bool]$Update = $false) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'Windows App Installer (winget) is missing. Install Microsoft App Installer, then rerun app_1_install.ps1.' }
    $savedPreference = $ErrorActionPreference
    try { $ErrorActionPreference = 'Continue'; & $winget.Source list --id $Id --exact --accept-source-agreements *> $null; $listExitCode=$LASTEXITCODE }
    finally { $ErrorActionPreference = $savedPreference }
    if ($listExitCode -eq 0 -and -not $Update) { Write-Host "Already installed: $Id"; return }
    $operation = if ($listExitCode -eq 0) { 'upgrade' } else { 'install' }
    Invoke-Checked $winget.Source @($operation,'--id',$Id,'--exact','--source','winget','--silent','--accept-package-agreements','--accept-source-agreements','--disable-interactivity')
    Refresh-Path
}
if ($Plan) {
    Write-Host 'PLAN ONLY: Python 3.12 -> VC++ runtime -> FFmpeg/ffprobe -> Node.js -> venv -> pinned Python libraries -> COLMAP CPU/CUDA -> Brush -> viewer/export assets -> mask weights -> Verify + HTTP smoke test.'
    Write-Host "Environment: $venv; device: $Device. No downloads, installations or jobs are started."
    exit 0
}
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'Windows x64 is required.' }
Refresh-Path
if ($Check) {
    if (-not (Test-Path -LiteralPath $python)) { throw 'Missing environment. Run app_1_install.ps1 first.' }
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'manage.py'),'verify','--device',$Device)
    exit 0
}
# Check before any package manager or Python environment mutation.
Get-ChildItem -LiteralPath (Join-Path $root 'web_data\jobs') -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $lock = Join-Path $_.FullName '.task.lock'
    if (Test-Path -LiteralPath $lock) {
        $record = Get-Content -LiteralPath $lock -Raw | ConvertFrom-Json
        if ($record.pid -and (Get-Process -Id $record.pid -ErrorAction SilentlyContinue)) { throw "Job $($_.Name) is running. Wait before installing; -Check is read-only." }
    }
}
$logDir = Join-Path $root 'tools\install-logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Transcript -Path (Join-Path $logDir ('windows-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')) | Out-Null
try {
    Write-Host '[1/7] Checking system prerequisites...'
    $basePython = Find-Python312
    if (-not $basePython) { Install-Winget 'Python.Python.3.12'; $basePython = Find-Python312 }
    if (-not $basePython) { throw 'Python 3.12 installation could not be located. Reopen PowerShell and rerun.' }
    Install-Winget 'Microsoft.VCRedist.2015+.x64'
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { Install-Winget 'Gyan.FFmpeg.Essentials' }
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { Install-Winget 'OpenJS.NodeJS.LTS' }
    elseif ([int]((& $node.Source --version).TrimStart('v').Split('.')[0]) -lt 20) { Install-Winget 'OpenJS.NodeJS.LTS' $true }
    Refresh-Path
    foreach ($tool in @('ffmpeg','ffprobe','node')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool was installed but is not on PATH; reopen PowerShell and rerun." }
    }
    Write-Host '[2/7] Preparing Python environment...'
    if (-not (Test-Path -LiteralPath $python)) { Invoke-Checked $basePython @('-m','venv',$venv) }
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'manage.py'),'python','--device',$Device)
    $selected = $Device
    if ($selected -eq 'auto') {
        $selected = 'cpu'
        $nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($nvidia) { & $nvidia.Source -L *> $null; if ($LASTEXITCODE -eq 0) { $selected = 'cuda' } }
    }
    Write-Host "[3/7] Installing COLMAP ($selected)..."
    & (Join-Path $PSScriptRoot 'colmap.ps1') -Device $selected
    Write-Host '[4/7] Installing Brush...'
    & (Join-Path $PSScriptRoot 'brush.ps1')
    Write-Host '[5/7] Preparing local viewer/export assets and segmentation weights...'
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'manage.py'),'assets')
    Write-Host '[6/7] Verifying packages, tools, GPU backends and HTTP startup...'
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'manage.py'),'verify','--device',$Device,'--smoke','--output',(Join-Path $logDir 'verify.json'))
    Write-Host '[7/7] Ready. Start the app:'
    Write-Host 'powershell -ExecutionPolicy Bypass -File .\app_2_run.ps1'
} finally { Stop-Transcript | Out-Null }
