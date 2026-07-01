@echo off
REM ─────────────────────────────────────────────────────────────────────
REM  CleanMesh Installer — build a single .exe from bootstrap.py
REM
REM  Output:  CleanMesh_Installer.exe   (~10 MB)
REM  Usage on this PC:
REM    - pip install pyinstaller  (auto below)
REM    - double-click this bat, or run: build_installer.bat
REM
REM  The resulting .exe is portable — copy it to any Windows PC and run.
REM  It will:
REM    1. Detect what's missing (NVIDIA / Python / Blender / WSL)
REM    2. Auto-install what it can (Blender via winget, WSL via wsl --install)
REM    3. Clone the project from GitHub OR use a sibling bundle/ folder
REM    4. Provision TripoSR + TRELLIS (weights via HuggingFace or bundle)
REM    5. Build the CleanMesh launcher .exe
REM    6. Smoke-test both servers
REM ─────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
chcp 65001 > nul
title CleanMesh Installer Builder

echo.
echo === Ensuring PyInstaller is installed ===
python -m pip install --user pyinstaller > nul 2>&1

echo.
echo === Building CleanMesh_Installer.exe ===
python -m PyInstaller ^
    --onefile ^
    --console ^
    --name CleanMesh_Installer ^
    --icon NONE ^
    --distpath . ^
    --workpath _build ^
    --specpath _build ^
    --clean ^
    bootstrap.py

if not exist "CleanMesh_Installer.exe" (
    echo.
    echo BUILD FAILED — check output above.
    pause
    exit /b 1
)

REM Clean build artifacts
if exist _build rmdir /S /Q _build

echo.
echo ============================================================
echo   Built: %CD%\CleanMesh_Installer.exe
echo ============================================================
echo.
echo  Distribute this .exe by:
echo    A. Alone   → target PC needs internet; downloads everything
echo    B. With a bundle folder next to the .exe (from migrate_pack.bat):
echo       Installer_Bundle\
echo         CleanMesh_Installer.exe
echo         bundle\Image3D\
echo         bundle\Ubuntu-22.04.tar   (optional)
echo         bundle\triposr_backend\   (optional)
echo.
echo  On the target PC:
echo    - Right-click CleanMesh_Installer.exe → Run as administrator
echo    - Follow the stage-by-stage log
echo    - Reboot if WSL was just enabled, then re-run the installer
echo.
pause
