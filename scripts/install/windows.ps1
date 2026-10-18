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
    # If a previous run was interrupted while winget was installing an MSI,
    # wait for that transaction instead of colliding with its file locks.
    $deadline = (Get-Date).AddMinutes(15)
    while (Get-Process -Name winget -ErrorAction SilentlyContinue) {
        if ((Get-Date) -ge $deadline) {
            throw "Another winget install is still running after 15 minutes. Let it finish, then rerun app_1_install.ps1 -Device $Device."
        }
        Write-Host 'Waiting for an existing winget package installation to finish...'
        Start-Sleep -Seconds 3
    }
    $savedPreference = $ErrorActionPreference
    try { $ErrorActionPreference = 'Continue'; & $winget.Source list --id $Id --exact --accept-source-agreements *> $null; $listExitCode=$LASTEXITCODE }
    finally { $ErrorActionPreference = $savedPreference }
    if ($listExitCode -eq 0 -and -not $Update) { Write-Host "Already installed: $Id"; return }
    $operation = if ($listExitCode -eq 0) { 'upgrade' } else { 'install' }
    try {
        Invoke-Checked $winget.Source @($operation,'--id',$Id,'--exact','--source','winget','--silent','--accept-package-agreements','--accept-source-agreements','--disable-interactivity')
    } catch {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw "Could not install $Id. This package may require administrator permission. Rerun app_1_install.ps1 from an elevated PowerShell window; run app_2_run.ps1 normally. Original error: $($_.Exception.Message)"
        }
        throw
    }
    Refresh-Path
}
if ($Plan) {
    Write-Host 'PLAN ONLY: Python 3.12 -> VC++ runtime -> FFmpeg/ffprobe -> Node.js -> venv -> GPU-specific Python libraries -> COLMAP CPU or verified CUDA/cuDSS -> Brush -> viewer/export assets -> mask weights -> Verify + HTTP smoke test.'
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
$lockPath = Join-Path $logDir '.install.lock'
try {
    # OpenOrCreate + FileShare.None also recovers cleanly after a hard stop:
    # Windows releases the exclusive handle when the old process exits.
    $installLock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch [IO.IOException] {
    throw 'Another app_1_install.ps1 process already owns the installer lock. Wait for it to finish before starting a second installation.'
}
$lockText = [Text.Encoding]::UTF8.GetBytes("pid=$PID`nstarted=$(Get-Date -Format o)`n")
$installLock.SetLength(0)
$installLock.Write($lockText, 0, $lockText.Length)
$installLock.Flush()
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
    $profilePath = Join-Path $root 'tools\install-logs\install-profile.json'
    if (Test-Path -LiteralPath $profilePath) {
        $profile = Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
        $selected = $profile.colmap_device
        Write-Host "Hardware profile: $($profile.name); masks: $($profile.mask_backend); Torch: $($profile.torch_variant)"
    }
    Write-Host "[3/7] Installing COLMAP ($selected)..."
    if ($selected -eq 'cuda') {
        Invoke-Checked $python @((Join-Path $PSScriptRoot 'colmap_gpu_ba.py'))
    } else {
        & (Join-Path $PSScriptRoot 'colmap.ps1') -Device $selected
    }
    Write-Host '[4/7] Installing Brush...'
    & (Join-Path $PSScriptRoot 'brush.ps1')
    Write-Host '[5/7] Preparing local viewer/export assets and segmentation weights...'
    Invoke-Checked $python @((Join-Path $PSScriptRoot 'manage.py'),'assets')
    Write-Host '[6/7] Verifying packages, tools, HTTP startup; running real CPU/GPU checks and timing every global-BA solver...'
    $verifyDevice = if ($selected -eq 'cuda') { 'cuda' } else { $Device }
    $verifyArgs = @((Join-Path $PSScriptRoot 'manage.py'),'verify','--device',$verifyDevice,'--smoke','--execute','--output',(Join-Path $logDir 'verify.json'))
    if ($selected -eq 'cuda') { $verifyArgs += '--cuda-smoke' }
    Invoke-Checked $python $verifyArgs
    Write-Host '[7/7] Ready. Start the app:'
    Write-Host 'powershell -ExecutionPolicy Bypass -File .\app_2_run.ps1'
} finally {
    try { Stop-Transcript | Out-Null }
    finally { $installLock.Dispose() }
}
