@echo off
setlocal
echo Stopping all HushFlow instances...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*whisper_flow*' -or $_.CommandLine -like '*hushflow_main*' -or $_.Name -eq 'HushFlow.exe' -or $_.Name -eq 'whisper-server.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[OK] Stopped PID ' + $_.ProcessId) }"
del /f /q "%LOCALAPPDATA%\HushFlow\hushflow.pid" >nul 2>&1
echo [OK] HushFlow stopped.
