$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Join-Chars {
    param([int[]]$Codes)
    return -join ($Codes | ForEach-Object { [char]$_ })
}

$digitalHumanLeaf = Join-Chars @(0x6570, 0x5B57, 0x4EBA, 0x7CFB, 0x7EDF)
$startLeaf = Join-Chars @(0x542F, 0x52A8, 0x6570, 0x5B57, 0x4EBA, 0x7CFB, 0x7EDF)
$voiceServiceLeaf = Join-Chars @(0x542F, 0x52A8, 0x6570, 0x5B57, 0x4EBA, 0x8BED, 0x97F3, 0x670D, 0x52A1)
$usageLeaf = Join-Chars @(0x4F7F, 0x7528, 0x8BF4, 0x660E)

$sourceDir = Join-Path $PSScriptRoot "dist"
$targetDir = Join-Path "E:\Epic UE Render" $digitalHumanLeaf
$serviceDir = Join-Path $targetDir "VoiceService"
$ueExe = Join-Path $targetDir "Windows\RDLightingKit.exe"

if (-not (Test-Path -LiteralPath $sourceDir)) {
    throw "Source dist directory not found: $sourceDir"
}

if (-not (Test-Path -LiteralPath $targetDir)) {
    throw "Target digital human directory not found: $targetDir"
}

if (-not (Test-Path -LiteralPath $ueExe)) {
    throw "UE5 frontend exe not found: $ueExe"
}

New-Item -ItemType Directory -Path $serviceDir -Force | Out-Null

$filesToCopy = @(
    "server2.exe",
    "Start_Server.bat",
    ($voiceServiceLeaf + ".bat"),
    ($usageLeaf + ".txt")
)

foreach ($fileName in $filesToCopy) {
    $sourcePath = Join-Path $sourceDir $fileName
    $targetPath = Join-Path $serviceDir $fileName

    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Required packaged file not found: $sourcePath"
    }

    Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
}

$targetOutputDir = Join-Path $serviceDir "tts_output"
New-Item -ItemType Directory -Path $targetOutputDir -Force | Out-Null

$launcherContent = @'
@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ========================================
echo Digital Human System Launcher
echo ========================================
echo.

set "SERVICE_DIR=%~dp0VoiceService"
set "SERVICE_EXE=%~dp0VoiceService\server2.exe"
set "UE_DIR=%~dp0Windows"
set "UE_EXE=%~dp0Windows\RDLightingKit.exe"
set "LOCK_DIR=%TEMP%\digital_human_system_start.lock"

if not exist "%SERVICE_EXE%" (
    echo [ERROR] server2.exe was not found.
    echo Please make sure the voice service has been deployed into this folder.
    echo.
    pause
    exit /b 1
)

if not exist "%UE_EXE%" (
    echo [ERROR] RDLightingKit.exe was not found.
    echo Expected path:
    echo %UE_EXE%
    echo.
    pause
    exit /b 1
)

mkdir "%LOCK_DIR%" 2>nul
if errorlevel 1 (
    echo Another startup is already in progress.
    echo Opening voice control page...
    start "" "http://127.0.0.1:5000/"
    echo Opening UE5 frontend directly...
    start "RDLightingKit" /D "%UE_DIR%" "%UE_EXE%"
    exit /b 0
)

echo [1/3] Checking whether the voice service is already running...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:5000/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto START_UE

echo Checking whether port 5000 is already occupied...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
    echo Port 5000 is already occupied, but the voice service health check failed.
    echo Please close the other program using port 5000, then run this bat again.
    echo.
    rmdir "%LOCK_DIR%" 2>nul
    pause
    exit /b 1
)

echo [2/3] Starting local voice service...
start "Digital Human Voice Server" /D "%SERVICE_DIR%" "%SERVICE_EXE%"

echo Waiting for the voice service to become ready...
for /l %%i in (1,1,45) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:5000/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
    if not errorlevel 1 goto START_UE
    timeout /t 1 >nul
)

echo.
echo [ERROR] The voice service did not start.
echo Possible reasons:
echo 1. Windows Security or antivirus blocked server2.exe.
echo 2. Port 5000 is occupied by another program.
echo 3. The package was not fully copied.
echo.
rmdir "%LOCK_DIR%" 2>nul
pause
exit /b 1

:START_UE
echo Opening voice control page...
start "" "http://127.0.0.1:5000/"
echo [3/3] Starting UE5 frontend...
start "RDLightingKit" /D "%UE_DIR%" "%UE_EXE%"
echo Done.
rmdir "%LOCK_DIR%" 2>nul
exit /b 0
'@

$launcherPaths = @(
    (Join-Path $targetDir ($startLeaf + ".bat"))
)

foreach ($launcherPath in $launcherPaths) {
    Set-Content -LiteralPath $launcherPath -Value $launcherContent -Encoding UTF8
}

$readmePath = Join-Path $serviceDir "DigitalHuman_Start_Readme.txt"
$readmeContent = @'
Digital Human System Startup

1. Double-click Start_DigitalHuman.bat, or the Chinese launcher next to it.
2. The launcher first checks http://127.0.0.1:5000/health.
3. If the local voice service is not running, it starts server2.exe.
4. After the voice service is ready, it starts Windows\RDLightingKit.exe.
5. UE5 can keep using:
   ASR: http://127.0.0.1:5000/recognize_speech
   TTS: http://127.0.0.1:5000/generate_voice
'@

Set-Content -LiteralPath $readmePath -Value $readmeContent -Encoding UTF8

Write-Output "Deployment completed."
Write-Output "Target: $targetDir"
Write-Output "Voice service dir: $serviceDir"
Write-Output "Voice service: $(Join-Path $serviceDir 'server2.exe')"
Write-Output "UE5 frontend: $ueExe"
Write-Output "Launchers:"
foreach ($launcherPath in $launcherPaths) {
    Write-Output " - $launcherPath"
}
