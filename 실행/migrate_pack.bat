@echo off
REM ─────────────────────────────────────────────────────────────────────
REM  CleanMesh Studio - Migration Packager
REM  Creates a portable bundle for moving to another PC.
REM
REM  Usage:
REM    migrate_pack.bat              → defaults to D:\CleanMesh_Migration\
REM    migrate_pack.bat E:\MyBundle  → custom destination
REM
REM  Modes (you'll be asked):
REM    A) Code only          → ~10 MB   (re-download everything on new PC)
REM    B) Code + WSL export  → ~30 GB   (recommended; preserves model weights)
REM    C) Code + Windows venvs + WSL export → ~50 GB  (full)
REM ─────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
chcp 65001 > nul
title CleanMesh Migration Packager

set "DEST=%~1"
if "%DEST%"=="" set "DEST=D:\CleanMesh_Migration"

REM Project root is one level above 실행\
for %%I in ("%~dp0..") do set "HERE=%%~fI\"
set "TRIPOSR_DIR=C:\WorkingJob\3d-model-tool\python-backend"
set "TRELLIS_WIN=D:\trellis"

echo.
echo ============================================================
echo   CleanMesh Studio - Migration Packager
echo ============================================================
echo  Source project: %HERE%
echo  Destination:    %DEST%
echo.
echo  Pick a mode:
echo    [A] Code only           (~10 MB ; 3-4 hours setup on new PC)
echo    [B] Code + WSL export   (~30 GB ; 30 min setup, RECOMMENDED)
echo    [C] Full (code + venvs + WSL) (~50 GB ; 15 min setup)
echo.
set /p MODE="Mode (A/B/C): "

if /i "%MODE%"=="A" goto MODE_A
if /i "%MODE%"=="B" goto MODE_B
if /i "%MODE%"=="C" goto MODE_C
echo Invalid mode.
pause
exit /b 1

:MODE_A
set "DO_TRIPOSR_VENV=0"
set "DO_WSL_EXPORT=0"
goto BUILD

:MODE_B
set "DO_TRIPOSR_VENV=0"
set "DO_WSL_EXPORT=1"
goto BUILD

:MODE_C
set "DO_TRIPOSR_VENV=1"
set "DO_WSL_EXPORT=1"
goto BUILD

:BUILD
echo.
echo ============================================================
echo  Building bundle (mode %MODE%)
echo ============================================================

if not exist "%DEST%" mkdir "%DEST%"
if not exist "%DEST%\bundle" mkdir "%DEST%\bundle"

REM ─── 1. Project code (always) ─────────────────────────────────
echo.
echo [1] Copying Image3D project (code only, excluding build/dist)...
robocopy "%HERE%" "%DEST%\bundle\Image3D" /E ^
    /XD build dist __pycache__ .pytest_cache .git node_modules ^
    /XF *.log *.tmp ^
    /NFL /NDL /NJH /NJS /NC /NS /NP
echo     done.

REM ─── 2. D:\trellis Windows-side scripts ──────────────────────
echo.
echo [2] Copying D:\trellis runner + install scripts (workdir excluded)...
if exist "%TRELLIS_WIN%" (
    robocopy "%TRELLIS_WIN%" "%DEST%\bundle\trellis_win" /E ^
        /XD _workdir __pycache__ ^
        /XF *.log *.tmp ^
        /NFL /NDL /NJH /NJS /NC /NS /NP
    echo     done.
) else (
    echo     SKIPPED — %TRELLIS_WIN% not found
)

REM ─── 3. TripoSR venv (mode C only) ───────────────────────────
if "%DO_TRIPOSR_VENV%"=="1" (
    echo.
    echo [3] Copying TripoSR backend (incl. venv ~15 GB)...
    if exist "%TRIPOSR_DIR%" (
        robocopy "%TRIPOSR_DIR%" "%DEST%\bundle\triposr_backend" /E ^
            /XD __pycache__ .git ^
            /NFL /NDL /NJH /NJS /NC /NS /NP
        echo     done.
    ) else (
        echo     SKIPPED — %TRIPOSR_DIR% not found
    )
) else (
    echo.
    echo [3] Skipping TripoSR venv (will re-install on new PC via pip).
)

REM ─── 4. WSL distro export (modes B and C) ─────────────────────
if "%DO_WSL_EXPORT%"=="1" (
    echo.
    echo [4] Exporting WSL Ubuntu-22.04 (this takes ~5-15 minutes)...
    wsl --export Ubuntu-22.04 "%DEST%\bundle\Ubuntu-22.04.tar"
    if errorlevel 1 (
        echo     WARNING: wsl --export failed. Make sure WSL is installed.
    ) else (
        echo     done. (TRELLIS venv + model weights are inside this tar)
    )
) else (
    echo.
    echo [4] Skipping WSL export (will reinstall TRELLIS + redownload weights on new PC).
)

REM ─── 5. Write the setup script for the new PC ────────────────
echo.
echo [5] Writing setup_on_new_pc.bat...
(
echo @echo off
echo REM Run this on the NEW PC after copying the bundle there.
echo REM Adjust the DEST_* paths below if you want different install locations.
echo.
echo setlocal EnableDelayedExpansion
echo chcp 65001 ^> nul
echo title CleanMesh Setup ^(new PC^)
echo.
echo set "BUNDLE=%%~dp0"
echo set "DEST_PROJECT=%%USERPROFILE%%\Desktop\Image3D"
echo set "DEST_TRIPOSR=C:\WorkingJob\3d-model-tool\python-backend"
echo set "DEST_TRELLIS_WIN=D:\trellis"
echo.
echo echo Bundle: %%BUNDLE%%
echo echo.
echo echo Will copy to:
echo echo   - %%DEST_PROJECT%%
echo echo   - %%DEST_TRIPOSR%%   ^(if present in bundle^)
echo echo   - %%DEST_TRELLIS_WIN%%
echo echo.
echo set /p OK="Continue? (y/n): "
echo if /i not "%%OK%%"=="y" exit /b 0
echo.
echo REM 1. project
echo robocopy "%%BUNDLE%%bundle\Image3D" "%%DEST_PROJECT%%" /E /NFL /NDL /NJH /NJS
echo.
echo REM 2. triposr
echo if exist "%%BUNDLE%%bundle\triposr_backend" ^(
echo     robocopy "%%BUNDLE%%bundle\triposr_backend" "%%DEST_TRIPOSR%%" /E /NFL /NDL /NJH /NJS
echo ^) else ^(
echo     echo TripoSR not in bundle — clone manually:
echo     echo   git clone https://github.com/VAST-AI-Research/TripoSR "%%DEST_TRIPOSR%%"
echo     echo   cd "%%DEST_TRIPOSR%%"
echo     echo   python -m venv venv ^&^& venv\Scripts\activate ^&^& pip install -r requirements.txt
echo ^)
echo.
echo REM 3. trellis windows-side
echo robocopy "%%BUNDLE%%bundle\trellis_win" "%%DEST_TRELLIS_WIN%%" /E /NFL /NDL /NJH /NJS
echo if not exist "%%DEST_TRELLIS_WIN%%\_workdir" mkdir "%%DEST_TRELLIS_WIN%%\_workdir"
echo.
echo REM 4. WSL import
echo if exist "%%BUNDLE%%bundle\Ubuntu-22.04.tar" ^(
echo     echo Importing WSL distro... ^(uses C:\WSL\Ubuntu-22.04\^)
echo     if not exist "C:\WSL" mkdir "C:\WSL"
echo     wsl --import Ubuntu-22.04 "C:\WSL\Ubuntu-22.04" "%%BUNDLE%%bundle\Ubuntu-22.04.tar"
echo ^) else ^(
echo     echo WSL distro not in bundle — install manually:
echo     echo   wsl --install Ubuntu-22.04
echo     echo   then re-run trellis_win\_install_step1.sh inside WSL
echo ^)
echo.
echo REM 5. logs dir
echo if not exist "C:\t3d\logs" mkdir "C:\t3d\logs"
echo.
echo REM 6. Build the .exe on new PC ^(python required^)
echo cd /d "%%DEST_PROJECT%%"
echo if exist build_launcher.bat call build_launcher.bat
echo.
echo echo Setup complete.
echo echo Double-click %%DEST_PROJECT%%\CleanMeshStudio.exe to start.
echo pause
) > "%DEST%\setup_on_new_pc.bat"

REM ─── 6. Write a README ───────────────────────────────────────
(
echo CleanMesh Studio - Migration Bundle
echo ====================================
echo.
echo Source PC:    %COMPUTERNAME%
echo Created:      %DATE% %TIME%
echo Mode:         %MODE%
echo.
echo Contents:
echo   bundle\Image3D\          - main project ^(code, exe, web UI^)
echo   bundle\trellis_win\      - D:\trellis runner + install scripts
if "%DO_TRIPOSR_VENV%"=="1" echo   bundle\triposr_backend\  - TripoSR backend ^(incl. venv, large^)
if "%DO_WSL_EXPORT%"=="1"   echo   bundle\Ubuntu-22.04.tar  - exported WSL distro ^(incl. TRELLIS weights^)
echo   setup_on_new_pc.bat      - run this on the destination PC
echo.
echo Pre-requisites on the new PC:
echo   1. NVIDIA driver + CUDA 12.1 runtime
echo   2. Python 3.11 ^(python.org^)
if "%DO_WSL_EXPORT%"=="0" echo   3. WSL2 + Ubuntu 22.04: wsl --install Ubuntu-22.04
echo   3/4. Blender 5.1.2 from blender.org
echo   5. Git from git-scm.com
echo.
echo Setup steps on new PC:
echo   1. Copy the entire bundle to the new PC.
echo   2. Double-click setup_on_new_pc.bat.
echo   3. Wait for it to finish.
echo   4. Double-click Image3D\CleanMeshStudio.exe.
) > "%DEST%\README.txt"

echo.
echo ============================================================
echo  Done!
echo ============================================================
echo  Bundle:  %DEST%
echo  README:  %DEST%\README.txt
echo  Setup:   %DEST%\setup_on_new_pc.bat
echo.
dir /B "%DEST%"
echo.
pause
