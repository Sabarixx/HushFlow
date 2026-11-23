@echo off
setlocal enabledelayedexpansion
title WhisperFlow - 1-Click Automated Installer
cd /d "%~dp0"

echo ========================================================
echo    WHISPERFLOW - AUTOMATED 1-CLICK INSTALLER
echo    100%% Local, Zero-Token Push-to-Talk AI Voice Typing
echo ========================================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not found in system PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo [OK] %%v detected.

:: 2. Install Dependencies
echo.
echo [*] Installing required Python libraries...
pip install -r requirements.txt --quiet --disable-pip-version-check
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Retrying pip install with --user flag...
    pip install -r requirements.txt --user --quiet
)
echo [OK] Dependencies successfully installed.

:: 3. Download AI Model if not present
echo.
if not exist "ggml-small.en-q5_1.bin" (
    echo [*] Downloading high-speed AI model: small.en-q5_1 (190 MB)...
    echo [*] Source: https://huggingface.co/ggerganov/whisper.cpp
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $wc = New-Object System.Net.WebClient; $wc.DownloadFile('https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en-q5_1.bin', 'ggml-small.en-q5_1.bin')"
    if exist "ggml-small.en-q5_1.bin" (
        echo [OK] AI Model downloaded successfully.
    ) else (
        echo [ERROR] Model download failed. Please check your internet connection.
        pause
        exit /b 1
    )
) else (
    echo [OK] AI Model binary already present (ggml-small.en-q5_1.bin).
)

:: 4. Create Desktop Shortcut
echo.
echo [*] Creating Desktop Shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $d = [Environment]::GetFolderPath('Desktop'); $s = $ws.CreateShortcut([System.IO.Path]::Combine($d, 'WhisperFlow.lnk')); $py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source; if (-not $py) { $py = 'pythonw.exe' }; $s.TargetPath = $py; $s.Arguments = '\"' + [System.IO.Path]::Combine('%~dp0', 'whisper_flow.py') + '\"'; $s.WorkingDirectory = '%~dp0'; if (Test-Path '%~dp0whisper_flow.ico') { $s.IconLocation = '%~dp0whisper_flow.ico' }; $s.Description = 'WhisperFlow - Local Voice Dictation'; $s.Save()"
echo [OK] Desktop Shortcut "WhisperFlow" created on Desktop!

:: 5. Launch
echo.
echo ========================================================
echo    INSTALLATION COMPLETE!
echo    Starting WhisperFlow now...
echo    Hotkey: Hold [Ctrl + Alt] to speak, release to type!
echo ========================================================
start "" "run.bat"
timeout /t 3 >nul
