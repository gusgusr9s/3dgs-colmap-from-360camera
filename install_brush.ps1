$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$version = 'v0.3.0'
$url = "https://github.com/ArthurBrussee/brush/releases/download/$version/brush-app-x86_64-pc-windows-msvc.zip"
$expectedHash = 'B68E3E9CF052D51BF3EE30776FA5A364DE7F2BA13B58443128FF797BB7BCFCD6'
$installDir = Join-Path $PSScriptRoot 'tools\brush'
$exe = Join-Path $installDir 'brush_app.exe'

if (Test-Path -LiteralPath $exe) {
    Write-Host "Brush already installed: $exe"
    & $exe --version
    exit 0
}

New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$archive = Join-Path ([IO.Path]::GetTempPath()) "brush-windows-$([guid]::NewGuid().ToString('N')).zip"
try {
    Write-Host "Downloading Brush $version for Windows..."
    Invoke-WebRequest -Uri $url -OutFile $archive
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        throw "Brush archive checksum mismatch. Expected $expectedHash; got $actualHash"
    }

    Expand-Archive -LiteralPath $archive -DestinationPath $installDir -Force
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "Brush archive did not contain brush_app.exe in $installDir"
    }
    Write-Host "Installed Brush to $exe"
    & $exe --version
} finally {
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
}
