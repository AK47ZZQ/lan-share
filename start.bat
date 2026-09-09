@echo off
title LAN Share
cd /d "%~dp0"

echo ===================================
echo   LAN Share - Starting services...
echo ===================================

REM Start FastAPI backend
REM Pin hermes venv python: bare "python" resolves to the uv base env which has
REM no fastapi and dies instantly (root cause of the mystery guardian failure).
set PYTHON=C:\Users\<你的用户名>\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
echo [1/2] Starting FastAPI on :8766 ...
start /B "" "%PYTHON%" server.py > logs\server.log 2>&1

REM Wait for FastAPI to be ready
timeout /t 2 /nobreak >nul

REM Start Caddy
echo [2/2] Starting Caddy on :8765 ...
start /B caddy.exe run --config Caddyfile --adapter caddyfile > logs\caddy.log 2>&1

timeout /t 1 /nobreak >nul

echo.
echo ===================================
echo   LAN Share is running!
echo   LAN:    https://192.168.1.100:8765
echo   Local:  https://localhost:8765
echo ===================================
echo.
echo Press Ctrl+C to stop, or close this window.
pause
