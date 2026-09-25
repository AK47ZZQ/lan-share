@echo off
REM ============================================================
REM LAN Share deployment single source of truth (07 review batch 5).
REM Every path/port/interpreter value used by the .bat scripts
REM (start/stop/install-service/backup) lives HERE. Change once.
REM Keep this file pure ASCII (bat/GBK encoding gotchas).
REM ============================================================

set "LANSHARE_ROOT=%~dp0"
REM 2026-09-21 migration: project now owns its own env (venv + pg + tools all in-repo).
REM Root stays reachable via the C:\lan-share junction (ASCII path); the values below
REM are written against that junction so they survive future repo moves unchanged.
set "LANSHARE_PYTHON=C:\lan-share\.venv\Scripts\python.exe"
set "LANSHARE_API_PORT=8766"
set "LANSHARE_LAN_PORT=8765"
set "LANSHARE_WEB_PORT=8767"
set "LANSHARE_FLUTTER_BIN=C:\lan-share\env\flutter\bin"
set "LANSHARE_PGDUMP=C:\lan-share\env\pgsql-bin\pg_dump.exe"
set "LANSHARE_PGPASSFILE=C:\lan-share\env\pginstall\pgpass.conf"
set "LANSHARE_BACKUP_DEST=C:\lan-share-backup"
