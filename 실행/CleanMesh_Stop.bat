@echo off
chcp 65001 > nul
title CleanMesh Stop
echo Stopping CleanMesh + TripoSR servers...

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8100 " ^| findstr "LISTENING"') do (
    echo   killing CleanMesh PID %%P
    taskkill /F /PID %%P > nul 2>&1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo   killing TripoSR  PID %%P
    taskkill /F /PID %%P > nul 2>&1
)

echo Done.
timeout /t 2 /nobreak > nul
