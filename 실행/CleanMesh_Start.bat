@echo off
REM ─────────────────────────────────────────────────────────────
REM  CleanMesh Studio Launcher
REM  - TripoSR FastAPI (port 8000)  : start if not running
REM  - CleanMesh FastAPI (port 8100): start if not running
REM  - Open browser → http://localhost:8100/
REM  Window stays open so you can read logs / Ctrl+C to stop.
REM ─────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
chcp 65001 > nul
title CleanMesh Studio Launcher

REM This .bat lives in 실행\ ; the actual Python project is one level up in 작업리소스\
for %%I in ("%~dp0..\작업리소스") do set "PROJECT=%%~fI\"
set "TRIPOSR_DIR=C:\WorkingJob\3d-model-tool\python-backend"
set "TRIPOSR_PY=%TRIPOSR_DIR%\venv\Scripts\python.exe"
set "LOGS=C:\t3d\logs"
if not exist "%LOGS%" mkdir "%LOGS%"

echo.
echo ============================================================
echo   CleanMesh Studio — Launcher
echo ============================================================
echo  Project: %PROJECT%
echo  Logs:    %LOGS%
echo.

REM ─── 1. TripoSR server (port 8000) ───
echo [1/3] Checking TripoSR server (port 8000)...
netstat -ano | findstr ":8000 " | findstr "LISTENING" > nul
if errorlevel 1 (
    echo       not running — starting in background
    if exist "%TRIPOSR_PY%" (
        start "TripoSR Backend" /MIN cmd /c ""%TRIPOSR_PY%" "%TRIPOSR_DIR%\main.py" > "%LOGS%\triposr.log" 2>&1"
        echo       launched (logs: %LOGS%\triposr.log^)
    ) else (
        echo       SKIPPED — TripoSR venv not found at %TRIPOSR_PY%
    )
) else (
    echo       already running OK
)

REM ─── 2. CleanMesh server (port 8100) ───
echo.
echo [2/3] Checking CleanMesh server (port 8100)...
netstat -ano | findstr ":8100 " | findstr "LISTENING" > nul
if errorlevel 1 (
    echo       not running — starting in background
    pushd "%PROJECT%"
    start "CleanMesh API" /MIN cmd /c "python -m uvicorn server.main:app --host 0.0.0.0 --port 8100 > "%LOGS%\cleanmesh.log" 2>&1"
    popd
    echo       launched (logs: %LOGS%\cleanmesh.log^)
    echo       waiting 5s for server warmup...
    timeout /t 5 /nobreak > nul
) else (
    echo       already running OK
)

REM ─── 3. Open browser ───
echo.
echo [3/3] Opening browser → http://localhost:8100/
start "" "http://localhost:8100/"

echo.
echo ============================================================
echo  Ready! Page should appear in your default browser.
echo.
echo  - Close this window when you are done
echo  - Servers keep running in their own MINIMIZED cmd windows
echo  - To fully stop: close those minimized windows
echo ============================================================
echo.
echo Press any key to exit this launcher (servers keep running)...
pause > nul
