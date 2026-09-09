@echo off
REM Stop LAN Share. Preferred path: stop the NSSM services (net stop).
REM Fallback: kill whatever still listens on the API/Caddy ports.
REM (Old approach matched "server.py" in any python.exe command line - slow,
REM  wmic-dependent and could kill an unrelated process; port-based is precise.)

call "%~dp0env.bat"
net stop lan-share-api >nul 2>&1
net stop lan-share-caddy >nul 2>&1

for %%P in (%LANSHARE_API_PORT% %LANSHARE_LAN_PORT%) do (
    for /f %%K in ('powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %%P -State Listen -ErrorAction SilentlyContinue).OwningProcess"') do (
        echo Killing leftover PID %%K on port %%P
        taskkill /PID %%K /F >nul 2>&1
    )
)

echo LAN Share stopped.
