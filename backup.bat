@echo off
REM LAN Share backup (v3.5): PostgreSQL dump + data dir -> backup dest. Daily via Task Scheduler:
REM   schtasks /Create /TN "lan-share-backup" /SC DAILY /ST 03:30 /TR "C:\lan-share\backup.bat"
REM Requires PGPASSFILE (C:\pginstall\pgpass.conf) - password never lives in this file.
setlocal
set PGDUMP=C:\env\pgsql\bin\pg_dump.exe
set PGPASSFILE=C:\pginstall\pgpass.conf
if "%BACKUP_DEST%"=="" set BACKUP_DEST=C:\lan-share-backup

echo === LAN Share backup to %BACKUP_DEST% ===
if not exist "%BACKUP_DEST%" mkdir "%BACKUP_DEST%"
if not exist "%BACKUP_DEST%\db" mkdir "%BACKUP_DEST%\db"

REM -- meta DB: PostgreSQL custom-format dump (v3.5 production DB) --
"%PGDUMP%" -h 127.0.0.1 -p 5433 -U lanshare -d lanshare -Fc -f "%BACKUP_DEST%\db\lanshare.dump"
if errorlevel 1 (
  echo BACKUP FAILED: pg_dump error
  exit /b 1
)
echo pg_dump ok

REM -- file data: incremental (copy newer, never mirror-delete) --
robocopy "C:\lan-share\data" "%BACKUP_DEST%\files" /E /XO /R:2 /W:5 ^
  /XD ".chunks" ".zips" "tmp" /XF ".meta.db" ".meta.db-wal" ".meta.db-shm" /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 (
  echo BACKUP FAILED robocopy exit %ERRORLEVEL%
  exit /b 1
)
echo BACKUP OK
