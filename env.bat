@echo off
REM ============================================================
REM LAN Share deployment single source of truth.
REM Every path/port/interpreter value used by the .bat scripts
REM (start/stop/install-service/backup) lives HERE. Change once.
REM Keep this file pure ASCII (bat/GBK encoding gotchas).
REM
REM Defaults are derived from this script's own location, so the
REM repo can be cloned anywhere. To override without editing,
REM define the same-named environment variable beforehand.
REM ============================================================

set "LANSHARE_ROOT=%~dp0"

if not defined LANSHARE_PYTHON       set "LANSHARE_PYTHON=%LANSHARE_ROOT%.venv\Scripts\python.exe"
if not defined LANSHARE_API_PORT     set "LANSHARE_API_PORT=8766"
if not defined LANSHARE_LAN_PORT     set "LANSHARE_LAN_PORT=8765"
if not defined LANSHARE_WEB_PORT     set "LANSHARE_WEB_PORT=8767"
if not defined LANSHARE_FLUTTER_BIN  set "LANSHARE_FLUTTER_BIN=%LANSHARE_ROOT%env\flutter\bin"
if not defined LANSHARE_PGDUMP       set "LANSHARE_PGDUMP=%LANSHARE_ROOT%env\pgsql-bin\pg_dump.exe"
if not defined LANSHARE_PGPASSFILE   set "LANSHARE_PGPASSFILE=%LANSHARE_ROOT%env\pginstall\pgpass.conf"
if not defined LANSHARE_BACKUP_DEST  set "LANSHARE_BACKUP_DEST=%LANSHARE_ROOT%..\lan-share-backup"
if not defined LANSHARE_NSSM         set "LANSHARE_NSSM=nssm"
if not defined LANSHARE_CADDY        set "LANSHARE_CADDY=%LANSHARE_ROOT%bin\caddy.exe"
