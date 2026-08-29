@echo off
REM LAN Share backup (v3.5): PostgreSQL dump + data dir -> backup dest. Daily via Task Scheduler:
REM   schtasks /Create /TN "lan-share-backup" /SC DAILY /ST 03:30 /TR "C:\lan-share\backup.bat"
REM DB dumps are date-stamped and only the last 2 days are kept (older deleted here).
REM Requires PGPASSFILE (C:\pginstall\pgpass.conf) - password never lives in this file.
setlocal
set PYTHON=C:\Users\<你的用户名>\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
set PGDUMP=C:\env\pgsql\bin\pg_dump.exe
set PGPASSFILE=C:\pginstall\pgpass.conf
if "%BACKUP_DEST%"=="" set BACKUP_DEST=C:\lan-share-backup

echo === LAN Share backup to %BACKUP_DEST% ===
if not exist "%BACKUP_DEST%" mkdir "%BACKUP_DEST%"
if not exist "%BACKUP_DEST%\db" mkdir "%BACKUP_DEST%\db"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set DSTAMP=%%i

REM -- meta DB: PostgreSQL custom-format dump, date-stamped, keep last 2 days --
"%PGDUMP%" -h 127.0.0.1 -p 5433 -U lanshare -d lanshare -Fc -f "%BACKUP_DEST%\db\lanshare-%DSTAMP%.dump"
if errorlevel 1 (
  echo BACKUP FAILED: pg_dump error
  exit /b 1
)
echo pg_dump ok: lanshare-%DSTAMP%.dump
if exist "%BACKUP_DEST%\db\lanshare.dump" del "%BACKUP_DEST%\db\lanshare.dump"

REM -- retention: delete dumps older than 2 days --
forfiles -p "%BACKUP_DEST%\db" -m lanshare-*.dump -d -2 -c "cmd /c del @path" 2>nul

REM -- file data: incremental (copy newer, never mirror-delete) --
robocopy "C:\lan-share\data" "%BACKUP_DEST%\files" /E /XO /R:2 /W:5 ^
  /XD ".chunks" ".zips" "tmp" /XF ".meta.db" ".meta.db-wal" ".meta.db-shm" /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 (
  echo BACKUP FAILED robocopy exit %ERRORLEVEL%
  exit /b 1
)
echo BACKUP OK
endlocal
