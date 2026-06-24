@echo off
REM ─────────────────────────────────────────────────────────────
REM  Build CleanMeshStudio.exe with PyInstaller (--onefile)
REM  Output: %~dp0CleanMeshStudio.exe
REM ─────────────────────────────────────────────────────────────

chcp 65001 > nul
title Build CleanMeshStudio.exe

setlocal EnableDelayedExpansion

set "HERE=%~dp0"
REM The launcher source lives in ..\작업리소스\cleanmesh_launcher.py
for %%I in ("%HERE%..\작업리소스\cleanmesh_launcher.py") do set "ENTRY=%%~fI"

if not exist "%ENTRY%" (
    echo [ERROR] entry script not found: %ENTRY%
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Building CleanMeshStudio.exe
echo ============================================================
echo  Entry:  %ENTRY%
echo  Out:    %HERE%CleanMeshStudio.exe
echo.

REM 1. Ensure PyInstaller is installed
echo [1/3] Ensuring PyInstaller is installed...
python -m pip install --upgrade --quiet pyinstaller
if errorlevel 1 (
    echo [ERROR] failed to install PyInstaller
    pause
    exit /b 1
)

REM 2. Wipe previous build artifacts
echo.
echo [2/3] Cleaning previous build...
if exist "%HERE%build"            rmdir /S /Q "%HERE%build"
if exist "%HERE%dist"              rmdir /S /Q "%HERE%dist"
if exist "%HERE%CleanMeshStudio.spec" del /Q "%HERE%CleanMeshStudio.spec"

REM 3. Build
echo.
echo [3/3] Running PyInstaller...
python -m PyInstaller ^
    --onefile ^
    --console ^
    --name CleanMeshStudio ^
    --distpath "%HERE%" ^
    --workpath "%HERE%build" ^
    --specpath "%HERE%build" ^
    --clean ^
    --noconfirm ^
    "%ENTRY%"

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed.
    pause
    exit /b 1
)

echo.
if exist "%HERE%CleanMeshStudio.exe" (
    echo ============================================================
    echo   SUCCESS - CleanMeshStudio.exe created
    echo ============================================================
    echo  Path: %HERE%CleanMeshStudio.exe
    echo.
    echo  Logs will be written to:
    echo    %HERE%CleanMeshStudio.log     (launcher itself)
    echo    C:\t3d\logs\cleanmesh.log     (CleanMesh server)
    echo    C:\t3d\logs\triposr.log       (TripoSR server)
    echo.
    echo  You can now delete the build\ folder if you like:
    echo    rmdir /S /Q "%HERE%build"
) else (
    echo [ERROR] build claimed success but exe was not produced.
)
echo.
pause
