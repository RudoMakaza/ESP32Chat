@echo off
title ESP32 Chat - Stop
echo Stopping ESP32 chat services (cloud + web participants)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'sim_server|web_app' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; 'Done - all ESP32 chat processes stopped.'"
timeout /t 2 >nul