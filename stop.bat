@echo off
echo Stopping LAN Share services...

REM Kill FastAPI (python server.py)
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" ^| findstr python') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr "server.py" >nul && (
        echo Killing FastAPI (PID %%a)
        taskkill /PID %%a /F >nul 2>&1
    )
)

REM Kill Caddy
taskkill /IM caddy.exe /F >nul 2>&1

echo LAN Share stopped.
