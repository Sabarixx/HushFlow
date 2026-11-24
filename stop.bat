@echo off
setlocal
echo Stopping all WhisperFlow instances...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*whisper_flow.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('[OK] Stopped WhisperFlow PID ' + $_.ProcessId) }"
powershell -NoProfile -Command "Get-Process -Name 'whisper-server' -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force; Write-Host ('[OK] Stopped whisper-server PID ' + $_.Id) }"
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Host ('[OK] Freed Port 8080 PID ' + $_) }"
del /f /q "%~dp0whisper_flow.pid" >nul 2>&1
echo [OK] WhisperFlow stopped.
ping 127.0.0.1 -n 2 >nul
