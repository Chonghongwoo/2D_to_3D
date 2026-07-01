@echo off
REM ─────────────────────────────────────────────────────────────────────
REM  CleanMesh Studio - Migration Packager v1.2
REM  Creates a portable bundle for moving to another PC.
REM
REM  Usage:
REM    migrate_pack.bat              → defaults to D:\CleanMesh_Migration\
REM    migrate_pack.bat E:\MyBundle  → custom destination
REM
REM  Modes (you'll be asked):
REM    A) Code only          → ~10 MB   (re-download everything on new PC)
REM    B) Code + WSL export  → ~30 GB   (recommended; preserves model weights)
REM    C) Full  (code + Windows venvs + WSL) → ~50 GB
REM ─────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
chcp 65001 > nul
title CleanMesh Migration Packager v1.2

set "DEST=%~1"
if "%DEST%"=="" set "DEST=D:\CleanMesh_Migration"

REM Project root is one level above 실행\
for %%I in ("%~dp0..") do set "HERE=%%~fI\"
set "TRIPOSR_DIR=C:\WorkingJob\3d-model-tool\python-backend"
set "TRELLIS_WIN=D:\trellis"

echo.
echo ============================================================
echo   CleanMesh Studio - Migration Packager v1.2
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
echo [1] Copying project source (code only, excluding build/dist)...
robocopy "%HERE%" "%DEST%\bundle\Image3D" /E ^
    /XD build dist __pycache__ .pytest_cache .git node_modules ^
    /XF *.log *.tmp *.pyc ^
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
    echo [3] Copying TripoSR backend including venv ~15 GB...
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
    echo [4] Exporting WSL Ubuntu-22.04 (takes 5-15 minutes)...
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

REM ─── 5. Write the pre-check script ───────────────────────────
echo.
echo [5a] Writing precheck_on_new_pc.bat (runs first, reports missing deps)...
(
echo @echo off
echo REM Runs BEFORE setup_on_new_pc.bat to verify the target PC.
echo REM Reports each dependency as OK / MISSING / DEGRADED.
echo.
echo setlocal EnableDelayedExpansion
echo chcp 65001 ^> nul
echo title CleanMesh Pre-flight Check
echo.
echo echo ============================================================
echo echo   CleanMesh - Target PC Pre-check
echo echo ============================================================
echo echo.
echo set /a PROBLEMS=0
echo.
echo REM 1. NVIDIA GPU
echo where nvidia-smi ^>nul 2^>^&1
echo if errorlevel 1 ^(
echo     echo [X] NVIDIA GPU / driver: NOT FOUND
echo     echo     Install NVIDIA driver: https://www.nvidia.com/download/index.aspx
echo     set /a PROBLEMS+=1
echo ^) else ^(
echo     for /f "tokens=1 delims=," %%%%V in ^('nvidia-smi --query-gpu^=memory.total --format^=csv^,noheader^,nounits'^) do set VRAM^^^=%%%%V
echo     echo [OK] NVIDIA GPU present ^(VRAM: !VRAM! MiB^)
echo     if !VRAM! LSS 6000 echo      WARNING: VRAM ^< 6 GB — TRELLIS may not work at all
echo ^)
echo.
echo REM 2. Python 3.11+
echo where python ^>nul 2^>^&1
echo if errorlevel 1 ^(
echo     echo [X] Python: NOT FOUND
echo     echo     Install Python 3.11: https://python.org
echo     set /a PROBLEMS+=1
echo ^) else ^(
echo     for /f "tokens=2" %%%%V in ^('python --version 2^^^>^^^&1'^) do set PYVER^^^=%%%%V
echo     echo [OK] Python !PYVER!
echo ^)
echo.
echo REM 3. WSL2
echo wsl --status ^>nul 2^>^&1
echo if errorlevel 1 ^(
echo     echo [X] WSL2: NOT INSTALLED
echo     echo     Run in admin PowerShell: wsl --install
echo     set /a PROBLEMS+=1
echo ^) else ^(
echo     echo [OK] WSL2 available
echo ^)
echo.
echo REM 4. Blender 5.1.x
echo set "BLENDER_EXE="
echo if exist "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
echo if exist "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
echo if exist "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
echo if "!BLENDER_EXE!"=="" ^(
echo     echo [X] Blender 5.x: NOT FOUND at standard location
echo     echo     Install Blender 5.1+: https://www.blender.org/download/
echo     set /a PROBLEMS+=1
echo ^) else ^(
echo     echo [OK] Blender: !BLENDER_EXE!
echo ^)
echo.
echo REM 5. Free disk on C:
echo for /f "tokens=3" %%%%D in ^('dir C:\ /-C ^^^| findstr "bytes free"'^) do set FREEC^^^=%%%%D
echo echo [i] Free disk on C: !FREEC! bytes
echo.
echo echo ============================================================
echo if !PROBLEMS! GTR 0 ^(
echo     echo   PROBLEMS: !PROBLEMS!  -- Fix these before running setup.
echo ^) else ^(
echo     echo   ALL CHECKS PASSED -- ready to run setup_on_new_pc.bat
echo ^)
echo echo ============================================================
echo pause
) > "%DEST%\precheck_on_new_pc.bat"

REM ─── 6. Write the setup script ────────────────────────────────
echo.
echo [5b] Writing setup_on_new_pc.bat (v2 with auto-detect + validation)...
(
echo @echo off
echo REM CleanMesh Setup v2 - runs on the NEW PC
echo REM 1. Auto-detects Blender path and updates config.py
echo REM 2. Copies project + backends
echo REM 3. Imports WSL distro (if bundled)
echo REM 4. pip installs missing packages (incl. PyInstaller)
echo REM 5. Builds .exe launcher
echo REM 6. Optionally starts servers to verify.
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
echo echo Bundle:         %%BUNDLE%%
echo echo Install to:
echo echo   Project:      %%DEST_PROJECT%%
echo echo   TripoSR:      %%DEST_TRIPOSR%%
echo echo   Trellis-Win:  %%DEST_TRELLIS_WIN%%
echo echo.
echo set /p OK="Continue with these paths? (y/n): "
echo if /i not "%%OK%%"=="y" exit /b 0
echo.
echo REM ── 1. Detect Blender path ────────────────────────────
echo set "BLENDER_EXE="
echo if exist "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
echo if exist "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
echo if exist "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
echo if "!BLENDER_EXE!"=="" ^(
echo     echo ERROR: Blender not found. Install first, then re-run.
echo     pause
echo     exit /b 1
echo ^)
echo echo [Blender] %%BLENDER_EXE%%
echo.
echo REM ── 2. Copy project ────────────────────────────────────
echo echo [1/6] Copying project code...
echo robocopy "%%BUNDLE%%bundle\Image3D" "%%DEST_PROJECT%%" /E /NFL /NDL /NJH /NJS
echo.
echo REM ── 3. TripoSR ─────────────────────────────────────────
echo if exist "%%BUNDLE%%bundle\triposr_backend" ^(
echo     echo [2/6] Copying TripoSR backend...
echo     robocopy "%%BUNDLE%%bundle\triposr_backend" "%%DEST_TRIPOSR%%" /E /NFL /NDL /NJH /NJS
echo ^) else ^(
echo     echo [2/6] TripoSR NOT bundled — clone + venv setup required.
echo     echo       git clone https://github.com/VAST-AI-Research/TripoSR "%%DEST_TRIPOSR%%"
echo     echo       cd "%%DEST_TRIPOSR%%" ^&^& python -m venv venv ^&^& venv\Scripts\activate ^&^& pip install -r requirements.txt
echo ^)
echo.
echo REM ── 4. TRELLIS Windows-side scripts ────────────────────
echo echo [3/6] Copying D:\trellis runner...
echo robocopy "%%BUNDLE%%bundle\trellis_win" "%%DEST_TRELLIS_WIN%%" /E /NFL /NDL /NJH /NJS
echo if not exist "%%DEST_TRELLIS_WIN%%\_workdir" mkdir "%%DEST_TRELLIS_WIN%%\_workdir"
echo.
echo REM ── 5. WSL import ──────────────────────────────────────
echo if exist "%%BUNDLE%%bundle\Ubuntu-22.04.tar" ^(
echo     echo [4/6] Importing WSL distro to C:\WSL\Ubuntu-22.04\...
echo     if not exist "C:\WSL" mkdir "C:\WSL"
echo     wsl --import Ubuntu-22.04 "C:\WSL\Ubuntu-22.04" "%%BUNDLE%%bundle\Ubuntu-22.04.tar"
echo ^) else ^(
echo     echo [4/6] WSL distro NOT bundled — install manually:
echo     echo       wsl --install Ubuntu-22.04
echo     echo       then run bash /mnt/d/trellis/_install_step1.sh inside WSL
echo ^)
echo.
echo REM ── 6. Update config.py with detected Blender path ─────
echo echo [5/6] Patching cleanmesh/config.py with detected Blender path...
echo set "CFG=%%DEST_PROJECT%%\작업리소스\cleanmesh\config.py"
echo if exist "%%CFG%%" ^(
echo     python -c "import sys,re; p=r'%%CFG%%'; s=open(p,encoding='utf-8').read(); s=re.sub(r'blender\.executable\s*=\s*.+', r'blender.executable = r\"%%BLENDER_EXE%%\"', s); open(p,'w',encoding='utf-8').write(s); print('  config.py patched')"
echo ^) else ^(
echo     echo   WARN: config.py not found at %%CFG%%
echo ^)
echo.
echo REM ── 7. Install PyInstaller + build .exe ────────────────
echo echo [6/6] pip install pyinstaller + build .exe...
echo python -m pip install --user pyinstaller ^>nul 2^>^&1
echo cd /d "%%DEST_PROJECT%%"
echo if exist "실행\build_launcher.bat" ^(
echo     call "실행\build_launcher.bat"
echo ^) else ^(
echo     echo   WARN: build_launcher.bat not found; you can copy the .exe from source PC instead.
echo ^)
echo.
echo REM ── 8. Logs dir + first-run smoke test ─────────────────
echo if not exist "C:\t3d\logs" mkdir "C:\t3d\logs"
echo.
echo echo.
echo echo ============================================================
echo echo   Setup complete.
echo echo ============================================================
echo echo   Project:  %%DEST_PROJECT%%
echo echo   Launcher: %%DEST_PROJECT%%\실행\CleanMeshStudio.exe
echo echo.
echo echo   Next steps:
echo echo     1. Double-click the launcher above.
echo echo     2. Wait ~30 sec for both servers to warm up.
echo echo     3. Browser opens automatically to http://localhost:8100/
echo echo.
echo pause
) > "%DEST%\setup_on_new_pc.bat"

REM ─── 7. Write a README ───────────────────────────────────────
(
echo CleanMesh Studio - Migration Bundle v1.2
echo ==========================================
echo.
echo Source PC:    %COMPUTERNAME%
echo Created:      %DATE% %TIME%
echo Mode:         %MODE%
echo.
echo Contents:
echo   bundle\Image3D\           - main project (code, exe, web UI)
echo   bundle\trellis_win\       - D:\trellis runner + install scripts
if "%DO_TRIPOSR_VENV%"=="1" echo   bundle\triposr_backend\   - TripoSR backend (incl. venv, ~2 GB)
if "%DO_WSL_EXPORT%"=="1"   echo   bundle\Ubuntu-22.04.tar   - exported WSL distro (incl. TRELLIS + SAM2 weights)
echo   precheck_on_new_pc.bat    - runs first: verifies target PC has required deps
echo   setup_on_new_pc.bat       - runs second: actually installs everything
echo.
echo Prerequisites on the NEW PC:
echo   1. NVIDIA GPU with driver 550+ (8+ GB VRAM recommended)
echo   2. Windows 10 21H2 or Windows 11
echo   3. Python 3.11 from python.org
echo   4. Blender 5.1+ from blender.org (installed to standard C:\Program Files path)
if "%DO_WSL_EXPORT%"=="0" echo   5. WSL2 + Ubuntu 22.04: run "wsl --install" in admin PowerShell
echo   %DO_WSL_EXPORT%. Git from git-scm.com (optional, only for updates)
echo.
echo Setup steps on NEW PC:
echo   1. Copy this entire bundle folder to the new PC.
echo   2. Double-click precheck_on_new_pc.bat  ^(reports missing dependencies^)
echo   3. Fix anything the precheck flagged.
echo   4. Double-click setup_on_new_pc.bat     ^(does the actual install^)
echo   5. Wait for completion (~15-30 min depending on mode).
echo   6. Double-click Desktop\Image3D\실행\CleanMeshStudio.exe
) > "%DEST%\README.txt"

echo.
echo ============================================================
echo  Done!
echo ============================================================
echo  Bundle:    %DEST%
echo  README:    %DEST%\README.txt
echo  Precheck:  %DEST%\precheck_on_new_pc.bat
echo  Setup:     %DEST%\setup_on_new_pc.bat
echo.
echo  On the NEW PC:
echo    1. Copy the whole "%DEST%" folder there
echo    2. Run precheck_on_new_pc.bat first
echo    3. If OK, run setup_on_new_pc.bat
echo.
dir /B "%DEST%"
echo.
pause
