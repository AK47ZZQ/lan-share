@echo off
REM LAN Share backup (M5): data dir -> second disk. Run daily via Task Scheduler.
REM   schtasks /Create /TN "lan-share-backup" /SC DAILY /ST 03:30 /TR "C:\lan-share\backup.bat"
REM PostgreSQL (future M1-PG): add  pg_dump -Fc lan_share > "%DEST%\db\lan_share.dump"
setlocal
set PYTHON=C:\Users\<你的用户名>\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
if "%BACKUP_DEST%"=="" set BACKUP_DEST=D:\lan-share-backup

echo === LAN Share backup to %BACKUP_DEST% ===
if not exist "%BACKUP_DEST%" mkdir "%BACKUP_DEST%"

REM -- meta DB: online consistent copy via sqlite backup API --
"%PYTHON%" -c "import sqlite3,os,sys; src=sqlite3.connect(os.path.join(os.environ.get('SHARE_ROOT','C:/lan-share/data'),'.meta.db')); dst=sqlite3.connect(os.path.join(os.environ.get('BACKUP_DEST','D:/lan-share-backup'),'.meta.db.bak')); src.backup(dst); dst.close(); src.close(); print('meta db backed up')"

REM -- file data: incremental (copy newer, never mirror-delete) --
robocopy "C:\lan-share\data" "%BACKUP_DEST%\files" /E /XO /R:2 /W:5 ^
  /XD ".chunks" ".zips" "tmp" /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 (
  echo BACKUP FAILED robocopy exit %ERRORLEVEL%
  exit /b 1
)
echo BACKUP OK
