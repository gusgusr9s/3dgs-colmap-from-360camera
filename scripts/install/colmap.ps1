param([ValidateSet('cpu','cuda')][string]$Device = 'cpu')
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$version = '4.2.1'
$variant = if ($Device -eq 'cuda') { 'cuda' } else { 'nocuda' }
$url = "https://github.com/colmap/colmap/releases/download/$version/colmap-x64-windows-$variant.zip"
$expectedHash = if ($Device -eq 'cuda') { 'E9C5CBD84C2EA986D2E970A2473FC2D2E6B34A2CDCF5D3DF2765C319A63AF881' } else { 'C493D88CB4A43F21AFBA42CB55BF786E5434A0420E73DB0BA1454C5D8B3686AE' }
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$installDir = Join-Path $root 'tools\colmap'
$exe = Join-Path $installDir 'bin\colmap.exe'

if (Test-Path -LiteralPath $exe) {
    $banner = (& $exe -h 2>&1 | Out-String)
    $installedVersion = ($banner -split "`n")[0]
    $hasCuda = $banner -match 'with CUDA|CUDA-enabled'
    if ($installedVersion -match "COLMAP\s+$([regex]::Escape($version))" -and ($hasCuda -eq ($Device -eq 'cuda'))) {
        Write-Host "COLMAP already installed: $exe"
        Write-Host "COLMAP $version is ready."
        exit 0
    }
    $projectRoot = [IO.Path]::GetFullPath($root).TrimEnd('\')
    $resolvedInstall = [IO.Path]::GetFullPath($installDir).TrimEnd('\')
    if (-not $resolvedInstall.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace COLMAP outside the project: $resolvedInstall"
    }
    Write-Host "Replacing incompatible $installedVersion with COLMAP $version..."
    Remove-Item -LiteralPath $resolvedInstall -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$archive = Join-Path ([IO.Path]::GetTempPath()) "colmap-windows-$([guid]::NewGuid().ToString('N')).zip"
try {
    Write-Host "Downloading COLMAP $version ($Device, native equirectangular support) for Windows..."
    Invoke-WebRequest -Uri $url -OutFile $archive
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        throw "COLMAP archive checksum mismatch. Expected $expectedHash; got $actualHash"
    }

    Expand-Archive -LiteralPath $archive -DestinationPath $installDir -Force
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "COLMAP archive did not contain bin\colmap.exe in $installDir"
    }
    & $exe -h *> $null
    if ($LASTEXITCODE -ne 0) { throw 'The downloaded COLMAP executable could not start.' }
    Write-Host "COLMAP $version installed: $exe"
} finally {
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
}
