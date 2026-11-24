@echo off
setlocal
cd /d "%~dp0"
start "" pythonw.exe "%~dp0whisper_flow_web.py"
timeout /t 1 /nobreak >nul
start "" http://127.0.0.1:5050
