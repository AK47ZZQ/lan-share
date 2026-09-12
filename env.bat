@echo off
REM ============================================================
REM LAN Share deployment single source of truth (07 review batch 5).
REM Every path/port/interpreter value used by the .bat scripts
REM (start/stop/install-service/backup) lives HERE. Change once.
REM Keep this file pure ASCII (bat/GBK encoding gotchas).
REM ============================================================

set "LANSHARE_ROOT=%~dp0"
set "LANSHARE_PYTHON=C:\Users\<你的用户名>\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
set "LANSHARE_API_PORT=8766"
set "LANSHARE_LAN_PORT=8765"
set "LANSHARE_WEB_PORT=8767"
set "LANSHARE_FLUTTER_BIN=C:\flutter\flutter\bin"
set "LANSHARE_PGDUMP=C:\env\pgsql\bin\pg_dump.exe"
set "LANSHARE_PGPASSFILE=C:\pginstall\pgpass.conf"
set "LANSHARE_BACKUP_DEST=C:\lan-share-backup"
