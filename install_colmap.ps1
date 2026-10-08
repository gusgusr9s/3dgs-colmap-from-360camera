$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$version = '4.2.1'
$url = "https://github.com/colmap/colmap/releases/download/$version/colmap-x64-windows-nocuda.zip"
$expectedHash = 'C493D88CB4A43F21AFBA42CB55BF786E5434A0420E73DB0BA1454C5D8B3686AE'
$installDir = Join-Path $PSScriptRoot 'tools\colmap'
$exe = Join-Path $installDir 'bin\colmap.exe'

if (Test-Path -LiteralPath $exe) {
    $installedVersion = (& $exe -h 2>&1 | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0) { throw 'The installed COLMAP executable could not start.' }
    if ($installedVersion -match "COLMAP\s+$([regex]::Escape($version))") {
        Write-Host "COLMAP already installed: $exe"
        Write-Host "COLMAP $version is ready."
        exit 0
    }
    $projectRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
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
    Write-Host "Downloading COLMAP $version (CPU build with native equirectangular support) for Windows..."
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
