@echo off
REM ============================================================
REM  OneDrive autostart disabler  -- REQUIRES ADMIN
REM
REM  Right-click this file -> "Run as administrator"
REM
REM  Effect: disables 3 OneDrive scheduled tasks so OneDrive
REM          does NOT auto-start at next logon.
REM ============================================================

title OneDrive autostart disabler (admin)

REM ----- admin check -----
net session > nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] This script needs administrator privileges.
    echo.
    echo HOW TO FIX:
    echo   1. Close this window.
    echo   2. Right-click this .bat file in Explorer.
    echo   3. Choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Disabling 3 OneDrive scheduled tasks
echo ============================================================
echo.

schtasks /Change /TN "\OneDrive Per-Machine Standalone Update Task" /Disable
schtasks /Change /TN "\OneDrive Reporting Task-S-1-5-21-3781923717-82155625-586997817-1001" /Disable
schtasks /Change /TN "\OneDrive Startup Task-S-1-5-21-3781923717-82155625-586997817-1001" /Disable

echo.
echo ============================================================
echo  Verifying status (look for "Status: Disabled" below):
echo ============================================================

schtasks /Query /TN "\OneDrive Per-Machine Standalone Update Task" /FO LIST | findstr /B "Status"
schtasks /Query /TN "\OneDrive Reporting Task-S-1-5-21-3781923717-82155625-586997817-1001" /FO LIST | findstr /B "Status"
schtasks /Query /TN "\OneDrive Startup Task-S-1-5-21-3781923717-82155625-586997817-1001" /FO LIST | findstr /B "Status"

echo.
echo Done. OneDrive will NOT auto-start after the next logon.
echo To roll back: see _onedrive_rollback_steps.txt
echo.
pause
