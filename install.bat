@echo off
setlocal enabledelayedexpansion
title HushFlow - Source Installer
cd /d "%~dp0"

echo ========================================================
echo    HUSHFLOW - AUTOMATED SOURCE INSTALLER
echo    100%% Local, Offline Push-to-Talk Voice Dictation
echo ========================================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python 3.10+ was not found on PATH.
    echo For zero-dependency installation, download HushFlow-Setup.exe from GitHub Releases,
    echo or install Python 3.10+ from https://www.python.org/
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo [OK] %%v detected.

:: 2. Install Dependencies (includes customtkinter, darkdetect, pystray, etc.)
echo.
echo [*] Installing required Python libraries...
pip install -r requirements.txt --quiet --disable-pip-version-check
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Retrying pip install with --user flag...
    pip install -r requirements.txt --user --quiet
)
echo [OK] Dependencies installed.

:: 3. Generate high-DPI HushFlow brand assets
python brand_assets.py >nul 2>&1

:: 4. Create Desktop Shortcut pointing cleanly to HushFlow Studio
echo [*] Creating HushFlow Desktop Shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $d = [Environment]::GetFolderPath('Desktop'); $s = $ws.CreateShortcut([System.IO.Path]::Combine($d, 'HushFlow.lnk')); $py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source; if (-not $py) { $py = 'pythonw.exe' }; $s.TargetPath = $py; $s.Arguments = '\"' + [System.IO.Path]::Combine('%~dp0', 'hushflow_main.py') + '\"'; $s.WorkingDirectory = '%~dp0'; if (Test-Path '%~dp0assets\hushflow.ico') { $s.IconLocation = '%~dp0assets\hushflow.ico' }; $s.Description = 'HushFlow - Private Offline Voice Dictation'; $s.Save()"
echo [OK] Shortcut "HushFlow" created on Desktop!

:: 5. Launch HushFlow Studio (handles model check/download & onboarding in GUI)
echo.
echo [*] Launching HushFlow Studio...
start "" pythonw.exe "%~dp0hushflow_main.py"
timeout /t 2 >nul
