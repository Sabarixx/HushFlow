<#
.SYNOPSIS
    Builds standalone HushFlow.exe (PyInstaller) and HushFlow-Setup-2.2.0.exe (Inno Setup 6).
    Automatically signs generated binaries & installer with -PfxPath, -CertThumbprint,
    or the local "HushFlow Local Code Signing" certificate in Cert:\CurrentUser\My.
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

if (-not $PfxPath -and -not $CertThumbprint) {
    $autoCert = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Subject -like "*HushFlow Local Code Signing*" -and
            $_.HasPrivateKey -and
            ($_.EnhancedKeyUsageList | Where-Object { $_.ObjectId -eq "1.3.6.1.5.5.7.3.3" })
        } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1
    if ($autoCert) {
        $CertThumbprint = $autoCert.Thumbprint
        Write-Host "[INFO] Auto-selected local Code Signing cert: $CertThumbprint" -ForegroundColor DarkGray
    }
}

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
        if ($LASTEXITCODE -ne 0) {
            & $signtool sign /fd SHA256 /f $PfxPath /p $PfxPassword $TargetFile
            if ($LASTEXITCODE -ne 0) { throw "signtool failed for $TargetFile" }
        }
    } elseif ($CertThumbprint) {
        & $signtool sign /fd SHA256 /sha1 $CertThumbprint /tr "http://timestamp.digicert.com" /td SHA256 $TargetFile
        if ($LASTEXITCODE -ne 0) {
            & $signtool sign /fd SHA256 /sha1 $CertThumbprint $TargetFile
            if ($LASTEXITCODE -ne 0) { throw "signtool failed for $TargetFile" }
        }
    }
}

if ($PfxPath -or $CertThumbprint) {
    Write-Host "[3b] Signing HushFlow.exe binary..." -ForegroundColor Cyan
    Invoke-CodeSign "$RootDir\dist\HushFlow\HushFlow.exe"
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

$SetupExe = "$RootDir\dist\HushFlow-Setup-2.2.0.exe"
if ($PfxPath -or $CertThumbprint) {
    Invoke-CodeSign $SetupExe
}

Write-Host "[5/5] Writing SHA-256 verification checksums..." -ForegroundColor Cyan
$ExeHash = (Get-FileHash "$RootDir\dist\HushFlow\HushFlow.exe" -Algorithm SHA256).Hash
$SrvHash = (Get-FileHash "$RootDir\dist\HushFlow\_internal\bin\whisper-server.exe" -Algorithm SHA256).Hash
$CliHash = (Get-FileHash "$RootDir\dist\HushFlow\_internal\bin\whisper-cli.exe" -Algorithm SHA256).Hash
$SetupHash = (Get-FileHash $SetupExe -Algorithm SHA256).Hash
"SHA256 HushFlow.exe: $ExeHash`r`nSHA256 whisper-server.exe: $SrvHash`r`nSHA256 whisper-cli.exe: $CliHash`r`nSHA256 HushFlow-Setup-2.2.0.exe: $SetupHash" | Set-Content "$RootDir\dist\CHECKSUMS.txt"

Write-Host "`n[SUCCESS] Build Complete:" -ForegroundColor Green
Write-Host "  Standalone App : $RootDir\dist\HushFlow\HushFlow.exe"
Write-Host "  Windows Setup  : $SetupExe"
Write-Host "  SHA-256 Hashes : $RootDir\dist\CHECKSUMS.txt"
