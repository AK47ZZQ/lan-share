@echo off
REM LAN Share — 注册为 Windows 系统服务（开机自启）
REM 需要管理员权限运行

set NSSM=C:\Users\<你的用户名>\.openclaw\nssm\nssm
set PROJECT=C:\lan-share
set PYTHON=C:\lan-share\.venv\Scripts\python.exe

echo ===================================
echo   Registering LAN Share services
echo ===================================

REM --- FastAPI 服务 ---
echo [1/2] Registering lan-share-api ...
"%NSSM%" install lan-share-api "%PYTHON%" "%PROJECT%\server.py"
"%NSSM%" set lan-share-api AppDirectory "%PROJECT%"
"%NSSM%" set lan-share-api DisplayName "LAN Share API"
"%NSSM%" set lan-share-api Description "LAN Share FastAPI backend (port 8766)"
"%NSSM%" set lan-share-api Start SERVICE_AUTO_START
"%NSSM%" set lan-share-api AppStdout "%PROJECT%\logs\api-stdout.log"
"%NSSM%" set lan-share-api AppStderr "%PROJECT%\logs\api-stderr.log"
"%NSSM%" set lan-share-api AppRotateFiles 1
"%NSSM%" set lan-share-api AppRotateOnline 1
"%NSSM%" set lan-share-api AppRotateBytes 10485760

REM --- Caddy 服务 ---
echo [2/2] Registering lan-share-caddy ...
"%NSSM%" install lan-share-caddy "%PROJECT%\caddy.exe" "run --config Caddyfile --adapter caddyfile"
"%NSSM%" set lan-share-caddy AppDirectory "%PROJECT%"
"%NSSM%" set lan-share-caddy DisplayName "LAN Share Caddy"
"%NSSM%" set lan-share-caddy Description "LAN Share Caddy reverse proxy (port 8765)"
"%NSSM%" set lan-share-caddy Start SERVICE_AUTO_START
"%NSSM%" set lan-share-caddy AppStdout "%PROJECT%\logs\caddy-stdout.log"
"%NSSM%" set lan-share-caddy AppStderr "%PROJECT%\logs\caddy-stderr.log"
"%NSSM%" set lan-share-caddy AppRotateFiles 1
"%NSSM%" set lan-share-caddy AppRotateOnline 1
"%NSSM%" set lan-share-caddy AppRotateBytes 10485760

echo.
echo ===================================
echo   Services registered!
echo   Start:  net start lan-share-api && net start lan-share-caddy
echo   Stop:   net stop lan-share-caddy && net stop lan-share-api
echo   Remove: nssm remove lan-share-api confirm
echo           nssm remove lan-share-caddy confirm
echo ===================================
pause
