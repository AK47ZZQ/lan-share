@echo off
title LAN Share
cd /d "%~dp0"
call "%~dp0env.bat"

echo ===================================
echo   LAN Share - Starting services...
echo ===================================

REM Start FastAPI backend. Python pinned via env.bat (single source of truth,
REM 07 review batch 5): bare "python" resolves to the uv base env which has no
REM fastapi and dies instantly (root cause of the mystery guardian failure).
echo [1/2] Starting FastAPI on :%LANSHARE_API_PORT% ...
start /B "" "%LANSHARE_PYTHON%" server.py > logs\server.log 2>&1

REM Wait for FastAPI to be ready
timeout /t 2 /nobreak >nul

REM Start Caddy
echo [2/2] Starting Caddy on :%LANSHARE_LAN_PORT% ...
start /B bin\caddy.exe run --config Caddyfile --adapter caddyfile > logs\caddy.log 2>&1

timeout /t 1 /nobreak >nul

echo.
echo ===================================
echo   LAN Share is running!
echo   LAN:    https://<本机IP>:8765
echo   Local:  https://localhost:8765
echo ===================================
echo.
echo Press Ctrl+C to stop, or close this window.
pause
