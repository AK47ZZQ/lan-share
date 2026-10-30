@echo off
REM LAN Share - register as Windows services (auto start)
REM Run as Administrator.
call "%~dp0env.bat"

echo ===================================
echo   Registering LAN Share services
echo   ROOT: %LANSHARE_ROOT%
echo ===================================

where "%LANSHARE_NSSM%" >nul 2>&1
if errorlevel 1 (
  echo [!] nssm not found. Put nssm.exe on PATH or set LANSHARE_NSSM.
  echo     https://nssm.cc/download
  exit /b 1
)
if not exist "%LANSHARE_PYTHON%" (
  echo [!] Python venv not found at %LANSHARE_PYTHON%
  echo     Create it first:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)
if not exist "%LANSHARE_CADDY%" (
  echo [!] caddy.exe not found at %LANSHARE_CADDY%
  echo     Download from https://caddyserver.com/download and place it in bin\
  exit /b 1
)

REM --- FastAPI service ---
echo [1/2] Registering lan-share-api ...
"%LANSHARE_NSSM%" install lan-share-api "%LANSHARE_PYTHON%" "%LANSHARE_ROOT%server.py"
"%LANSHARE_NSSM%" set lan-share-api AppDirectory "%LANSHARE_ROOT%"
"%LANSHARE_NSSM%" set lan-share-api DisplayName "LAN Share API"
"%LANSHARE_NSSM%" set lan-share-api Description "LAN Share FastAPI backend (port %LANSHARE_API_PORT%)"
"%LANSHARE_NSSM%" set lan-share-api Start SERVICE_AUTO_START
"%LANSHARE_NSSM%" set lan-share-api AppStdout "%LANSHARE_ROOT%logs\api-stdout.log"
"%LANSHARE_NSSM%" set lan-share-api AppStderr "%LANSHARE_ROOT%logs\api-stderr.log"
"%LANSHARE_NSSM%" set lan-share-api AppRotateFiles 1
"%LANSHARE_NSSM%" set lan-share-api AppRotateOnline 1
"%LANSHARE_NSSM%" set lan-share-api AppRotateBytes 10485760

REM --- Caddy service ---
echo [2/2] Registering lan-share-caddy ...
"%LANSHARE_NSSM%" install lan-share-caddy "%LANSHARE_CADDY%" "run --config Caddyfile --adapter caddyfile"
"%LANSHARE_NSSM%" set lan-share-caddy AppDirectory "%LANSHARE_ROOT%"
"%LANSHARE_NSSM%" set lan-share-caddy DisplayName "LAN Share Caddy"
"%LANSHARE_NSSM%" set lan-share-caddy Description "LAN Share Caddy reverse proxy (port %LANSHARE_LAN_PORT%)"
"%LANSHARE_NSSM%" set lan-share-caddy Start SERVICE_AUTO_START
"%LANSHARE_NSSM%" set lan-share-caddy AppStdout "%LANSHARE_ROOT%logs\caddy-stdout.log"
"%LANSHARE_NSSM%" set lan-share-caddy AppStderr "%LANSHARE_ROOT%logs\caddy-stderr.log"
"%LANSHARE_NSSM%" set lan-share-caddy AppRotateFiles 1
"%LANSHARE_NSSM%" set lan-share-caddy AppRotateOnline 1
"%LANSHARE_NSSM%" set lan-share-caddy AppRotateBytes 10485760

echo.
echo ===================================
echo   Services registered!
echo   Start:  net start lan-share-postgresql ^&^& net start lan-share-api ^&^& net start lan-share-caddy
echo   Stop:   net stop lan-share-caddy ^&^& net stop lan-share-api
echo   Remove: nssm remove lan-share-api confirm
echo           nssm remove lan-share-caddy confirm
echo ===================================
