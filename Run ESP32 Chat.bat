@echo off
setlocal
title ESP32 Chat - Localhost
cd /d "%~dp0"

rem --- locate Python ---------------------------------------------------------
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
"%PY%" --version >nul 2>&1 || set "PY=python"
"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found on this machine.
    echo Install Python 3.12 from https://python.org then run this again.
    pause
    exit /b 1
)

echo ==================================================
echo   ESP32 Chat - localhost
echo ==================================================

rem --- radio cloud (sim) ------------------------------------------------------
netstat -ano | findstr /r /c:":5555 .*LISTENING" >nul 2>&1
if errorlevel 1 (
    echo   [radio cloud ] starting sim_server on :5555
    start "ESP32 Chat - Radio Cloud" /min "%PY%" "%~dp0python\sim_server.py"
    ping -n 2 127.0.0.1 >nul
) else (
    echo   [radio cloud ] already running
)

rem --- participant A ----------------------------------------------------------
netstat -ano | findstr /r /c:":5000 .*LISTENING" >nul 2>&1
if errorlevel 1 (
    echo   [participant ] starting web app A on :5000
    start "ESP32 Chat - Participant A" /min "%PY%" "%~dp0web\web_app.py" --port 5000 --mode sim --sim-port 5555
) else (
    echo   [participant ] web app A already running
)

rem --- participant B ----------------------------------------------------------
netstat -ano | findstr /r /c:":5001 .*LISTENING" >nul 2>&1
if errorlevel 1 (
    echo   [participant ] starting web app B on :5001
    start "ESP32 Chat - Participant B" /min "%PY%" "%~dp0web\web_app.py" --port 5001 --mode sim --sim-port 5555
) else (
    echo   [participant ] web app B already running
)

rem --- give the servers a moment, then open the pages -------------------------
ping -n 3 127.0.0.1 >nul
echo   Opening  http://127.0.0.1:5000  and  http://127.0.0.1:5001
start "" http://127.0.0.1:5000
start "" http://127.0.0.1:5001
echo ==================================================
echo   Servers keep running in the background.
echo   Right-click the shortcut - Stop - to shut them down.
ping -n 5 127.0.0.1 >nul
exit /b 0