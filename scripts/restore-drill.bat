@echo off
REM LAN Share restore drill (9.9 review: backup hardening).
REM Run weekly by Task Scheduler, e.g.:
REM   schtasks /Create /TN "lan-share-restore-drill" /SC WEEKLY /D SUN /ST 04:00 /TR "C:\lan-share\scripts\restore-drill.bat"
REM
REM Tier 1 (this script, read-only, safe):
REM   - pick the newest dump in %BACKUP_DEST%\db
REM   - verify sha256 against the .sha256 sidecar written by backup.bat
REM   - pg_restore -l integrity check (a truncated/corrupt archive fails here)
REM   - report table-data section count
REM Tier 2 (manual, full restore into a scratch DB) is documented at the
REM bottom. The lanshare role has NO CREATEDB (checked 2026-09-09), so tier 2
REM needs a one-time privilege decision by the owner.
REM NOTE: keep this file pure ASCII (cmd mangles UTF-8 line structure).
setlocal
set PGRESTORE=C:\lan-share\env\pgsql\bin\pg_restore.exe
set PGPASSFILE=C:\lan-share\env\pginstall\pgpass.conf
set BACKUP_DEST=C:\lan-share-backup

echo === LAN Share restore drill ===

REM -- newest dump (dir /o-d = newest first) --
set NEWEST=
for /f "delims=" %%F in ('dir /b /o-d "%BACKUP_DEST%\db\lanshare-*.dump" 2^>nul') do (
    if not defined NEWEST set NEWEST=%%F
)
if not defined NEWEST (
    echo DRILL FAILED: no dump found in %BACKUP_DEST%\db
    exit /b 1
)
echo newest dump: %NEWEST%

REM -- sha256 verify against sidecar --
if not exist "%BACKUP_DEST%\db\%NEWEST:.dump=.sha256%" (
    echo sha256 sidecar missing - dump predates backup.bat hardening, skip
    goto toc
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$f='%BACKUP_DEST%\db\%NEWEST%'; $e=(Get-Content ($f -replace '\.dump$','.sha256') -ErrorAction Stop).Trim(); $a=(Get-FileHash -Algorithm SHA256 $f).Hash; if($a -ne $e){ Write-Host ('hash mismatch: expected ' + $e + ' got ' + $a); exit 1 }"
if errorlevel 1 (
    echo DRILL FAILED: sha256 mismatch - backup corrupted or tampered
    exit /b 1
)
echo sha256 ok

:toc
REM -- archive integrity: pg_restore -l reads the whole custom-format archive --
REM NOTE: do NOT use %TEMP% (Git Bash exports TEMP=/tmp -> invalid cmd path) and
REM do NOT call bare `find` (resolves to GNU find via Git's PATH, which parses
REM /c as the C:\ root and walks the whole drive). Both only bite when run from
REM a Git Bash shell; scheduled tasks are immune. We use BACKUP_DEST + PS count.
set TOC=%BACKUP_DEST%\restore-drill-toc.tmp
"%PGRESTORE%" -l "%BACKUP_DEST%\db\%NEWEST%" > "%TOC%" 2>nul
if errorlevel 1 (
    echo DRILL FAILED: pg_restore -l cannot read the archive ^(corrupt dump^)
    exit /b 1
)
powershell -NoProfile -Command "Write-Host ('table-data sections: ' + (Select-String -Path '%TOC%' -Pattern 'TABLE DATA').Count)"
del "%TOC%" >nul 2>&1

REM -- file-data manifest check (count + bytes vs backup.bat) --
REM    Catches a partial/failed robocopy run that the DB-dump checks cannot see.
REM    Parsed with regex on the raw content so it works whether the manifest
REM    uses CRLF or LF line endings.
if not exist "%BACKUP_DEST%\files-manifest.txt" (
    echo file manifest missing - backup predates hardening, skip
    goto drill_ok
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$c=Get-Content '%BACKUP_DEST%\files-manifest.txt' -Raw -ErrorAction Stop; $ef=[int64]([regex]::Match($c,'files=(\d+)').Groups[1].Value); $eb=[int64]([regex]::Match($c,'bytes=(\d+)').Groups[1].Value); $f=@(Get-ChildItem -LiteralPath '%BACKUP_DEST%\files' -Recurse -File -Force -ErrorAction SilentlyContinue); $s=($f | Measure-Object -Property Length -Sum).Sum; if($null -eq $s){$s=0}; Write-Host ('manifest    : files ' + $ef + ' -> ' + $f.Count + ' ; bytes ' + $eb + ' -> ' + [int64]$s); if($ef -ne $f.Count -or $eb -ne [int64]$s){ exit 1 }"
if errorlevel 1 (
    echo DRILL FAILED: file-data manifest mismatch - backup incomplete
    exit /b 1
)
echo file manifest ok

:drill_ok
echo DRILL OK ^(tier 1^): %NEWEST% is readable and hash-verified.
echo.
echo -- Tier 2 manual drill (full restore, run by hand when needed) --
echo   1. one-time: ALTER ROLE lanshare CREATEDB;  (or use a superuser)
echo   2. add a pgpass line: 127.0.0.1:5433:lanshare_drill:lanshare:^<pwd^>
echo   3. createdb via psql, then:
echo      pg_restore -h 127.0.0.1 -p 5433 -U lanshare -d lanshare_drill "%BACKUP_DEST%\db\%NEWEST%"
echo   4. sanity: SELECT count(*) FROM users / photos / files
echo   5. drop the scratch database when done
endlocal
