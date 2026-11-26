<#
.SYNOPSIS
    Builds standalone HushFlow.exe (PyInstaller) and HushFlow-Setup-2.1.0.exe (Inno Setup 6).
    Optionally signs all generated executables when a Code Signing certificate is provided.
#>
param(
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$CertThumbprint = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
Set-Location $RootDir

Write-Host "[1/5] Generating 4x supersampled HushFlow brand assets..." -ForegroundColor Cyan
python brand_assets.py
if ($LASTEXITCODE -ne 0) { throw "Brand asset generation failed." }

Write-Host "[2/5] Stopping any running HushFlow / whisper-server instances..." -ForegroundColor Cyan
Get-Process -Name "HushFlow", "whisper-server" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "[3/5] Building standalone HushFlow.exe via PyInstaller..." -ForegroundColor Cyan
if (Test-Path "$RootDir\build") { Remove-Item "$RootDir\build" -Recurse -Force }
if (Test-Path "$RootDir\dist\HushFlow") { Remove-Item "$RootDir\dist\HushFlow" -Recurse -Force }
pyinstaller --noconfirm --clean "$ScriptDir\HushFlow.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

function Invoke-CodeSign {
    param([string]$TargetFile)
    if (-not (Test-Path $TargetFile)) { return }
    $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if (-not $signtool) {
        $sdkTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter "signtool.exe" -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -like "*x64*" } | Select-Object -Last 1
        if ($sdkTool) { $signtool = $sdkTool.FullName }
    }
    if (-not $signtool) {
        Write-Warning "signtool.exe not found; skipping Authenticode signature for $TargetFile"
        return
    }
    if ($PfxPath -and (Test-Path $PfxPath)) {
        & $signtool sign /fd SHA256 /f $PfxPath /p $PfxPassword /tr "http://timestamp.digicert.com" /td SHA256 $TargetFile
    } elseif ($CertThumbprint) {
        & $signtool sign /fd SHA256 /sha1 $CertThumbprint /tr "http://timestamp.digicert.com" /td SHA256 $TargetFile
    }
}

if ($PfxPath -or $CertThumbprint) {
    Write-Host "[3b] Signing HushFlow binaries..." -ForegroundColor Cyan
    Invoke-CodeSign "$RootDir\dist\HushFlow\HushFlow.exe"
    Invoke-CodeSign "$RootDir\dist\HushFlow\_internal\bin\whisper-server.exe"
    Invoke-CodeSign "$RootDir\dist\HushFlow\_internal\bin\whisper-cli.exe"
}

Write-Host "[4/5] Compiling Windows Installer via Inno Setup 6..." -ForegroundColor Cyan
$isccCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$Iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) not found. Install via: winget install JRSoftware.InnoSetup" }

& $Iscc "$ScriptDir\HushFlow.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }

$SetupExe = "$RootDir\dist\HushFlow-Setup-2.1.0.exe"
if ($PfxPath -or $CertThumbprint) {
    Invoke-CodeSign $SetupExe
}

Write-Host "[5/5] Writing SHA-256 verification checksums..." -ForegroundColor Cyan
$ExeHash = (Get-FileHash "$RootDir\dist\HushFlow\HushFlow.exe" -Algorithm SHA256).Hash
$SetupHash = (Get-FileHash $SetupExe -Algorithm SHA256).Hash
"SHA256 HushFlow.exe: $ExeHash`r`nSHA256 HushFlow-Setup-2.1.0.exe: $SetupHash" | Set-Content "$RootDir\dist\CHECKSUMS.txt"

Write-Host "`n[SUCCESS] Build Complete:" -ForegroundColor Green
Write-Host "  Standalone App : $RootDir\dist\HushFlow\HushFlow.exe"
Write-Host "  Windows Setup  : $SetupExe"
Write-Host "  SHA-256 Hashes : $RootDir\dist\CHECKSUMS.txt"
