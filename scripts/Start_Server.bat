@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ========================================
echo Digital Human Voice Server Launcher
echo ========================================
echo.

set "LOCK_DIR=%TEMP%\digital_human_voice_server_start.lock"
mkdir "%LOCK_DIR%" 2>nul
if errorlevel 1 (
    echo Another startup is already in progress.
    echo Opening the control page...
    start "" "http://127.0.0.1:5000/"
    exit /b 0
)

if not exist "%~dp0server2.exe" (
    echo [ERROR] server2.exe was not found.
    echo Please extract the whole zip file first, then run this bat file.
    echo.
    rmdir "%LOCK_DIR%" 2>nul
    pause
    exit /b 1
)

echo [1/3] Checking whether the service is already running...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:5000/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto READY

echo Checking whether port 5000 is already occupied...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
    echo Port 5000 is already occupied, but the health check failed.
    echo Please close the other program using port 5000, then run this bat again.
    echo.
    rmdir "%LOCK_DIR%" 2>nul
    pause
    exit /b 1
)

echo [2/3] Starting server2.exe...
start "Digital Human Voice Server" /D "%~dp0" "%~dp0server2.exe"

echo [3/3] Waiting for the service to become ready...
for /l %%i in (1,1,45) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:5000/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
    if not errorlevel 1 goto READY
    timeout /t 1 >nul
)

echo.
echo [ERROR] The service did not start.
echo Possible reasons:
echo 1. Windows Security or antivirus blocked server2.exe.
echo 2. Port 5000 is occupied by another program.
echo 3. The zip file was not fully extracted.
echo.
echo Try double-clicking server2.exe directly and check the black console window.
echo.
rmdir "%LOCK_DIR%" 2>nul
pause
exit /b 1

:READY
echo Service is ready. Opening http://127.0.0.1:5000/
start "" "http://127.0.0.1:5000/"
rmdir "%LOCK_DIR%" 2>nul
exit /b 0
