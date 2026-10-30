@echo off
REM LAN Share backup (v3.5): PostgreSQL dump + data dir -> backup dest. Daily via Task Scheduler:
REM   schtasks /Create /TN "lan-share-backup" /SC DAILY /ST 23:30 /TR "C:\lan-share\backup.bat"
REM   NOTE (2026-09-10): this comment and docs/OPERATIONS.md previously said 03:30 while
REM   README/HANDOFF said 23:30. Observed dump mtimes (23:30, 00:33) match 23:30, so the
REM   docs are now aligned to 23:30. Confirm the registered task time with
REM   `schtasks /Query /TN "lan-share-backup" /V /FO LIST` and fix all four places together.
REM DB dumps are date-stamped and only the last 2 days are kept (older deleted here).
REM Requires PGPASSFILE (C:\pginstall\pgpass.conf) - password never lives in this file.
REM Single source of truth: all paths come from env.bat (07 review batch 5
REM deployment convergence, finished 2026-09-12). %~dp0 keeps the script
REM location-relative so the junction path and any future move both work.
call "%~dp0env.bat"
setlocal
set "PGDUMP=%LANSHARE_PGDUMP%"
set "PGPASSFILE=%LANSHARE_PGPASSFILE%"
REM (old PYTHON= line removed: never used by this script)
if "%BACKUP_DEST%"=="" set "BACKUP_DEST=%LANSHARE_BACKUP_DEST%"

echo === LAN Share backup to %BACKUP_DEST% ===
if not exist "%BACKUP_DEST%" mkdir "%BACKUP_DEST%"
if not exist "%BACKUP_DEST%\db" mkdir "%BACKUP_DEST%\db"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set DSTAMP=%%i

REM -- meta DB: PostgreSQL custom-format dump, date-stamped, keep last 2 days --
REM lock-wait-timeout: dump 拿不到表的 ACCESS SHARE 时 5s 内放弃而不是挂死
REM （改进建议 9.6-P2-4；该锁不阻塞业务读写，正常情况瞬时获得）
"%PGDUMP%" -h 127.0.0.1 -p 5433 -U lanshare -d lanshare -Fc --lock-wait-timeout=5000 -f "%BACKUP_DEST%\db\lanshare-%DSTAMP%.dump"
if errorlevel 1 (
  echo BACKUP FAILED: pg_dump error
  exit /b 1
)
echo pg_dump ok: lanshare-%DSTAMP%.dump

REM -- integrity: size sanity + sha256 sidecar (restore-drill.bat verifies it) --
REM A truncated/zero-byte dump used to pass silently; now a dump under 10KB
REM fails the backup, and every dump ships with a .sha256 for drill verification.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$f='%BACKUP_DEST%\db\lanshare-%DSTAMP%.dump'; $s=(Get-Item $f).Length; if($s -lt 10240){ Write-Host ('dump too small: ' + $s + ' bytes'); exit 1 }; (Get-FileHash -Algorithm SHA256 $f).Hash | Set-Content -Encoding ascii ($f -replace '\.dump$','.sha256')"
if errorlevel 1 (
  echo BACKUP FAILED: dump suspiciously small or unreadable
  exit /b 1
)
echo sha256 sidecar written

if exist "%BACKUP_DEST%\db\lanshare.dump" del "%BACKUP_DEST%\db\lanshare.dump"

REM -- retention: delete dumps older than 2 days --
forfiles -p "%BACKUP_DEST%\db" -m lanshare-*.dump -d -2 -c "cmd /c del @path" 2>nul
forfiles -p "%BACKUP_DEST%\db" -m lanshare-*.sha256 -d -2 -c "cmd /c del @path" 2>nul

REM -- file data: incremental (copy newer, never mirror-delete) --
REM /MT:2（改进建议 9.6-P2-4）：默认 /MT:8 全速复制会把磁盘 IO 吃满，
REM 备份窗口内 Web/App 的列表与缩略图请求明显变慢；压到 2 线程换平顺
robocopy "%LANSHARE_ROOT%data" "%BACKUP_DEST%\files" /E /XO /R:2 /W:5 /MT:2 ^
  /XD ".chunks" ".zips" "tmp" /XF ".meta.db" ".meta.db-wal" ".meta.db-shm" /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 (
  echo BACKUP FAILED robocopy exit %ERRORLEVEL%
  exit /b 1
)

REM -- file-data manifest: fingerprint (count + total bytes) of the files copy.
REM    robocopy is incremental and never mirror-deletes, so a partial run is
REM    otherwise invisible; restore-drill.bat compares against this manifest.
REM    Read-only enumeration; ~20GB/7.5k files costs a few seconds.
REM    NOTE: WriteAllLines with an explicit List is used because passing an
REM    array / -join expression to Set-Content collapsed it onto ONE line
REM    (verified 2026-09-10) - the manifest must be 3 separate lines.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$r='%BACKUP_DEST%\files'; $f=@(Get-ChildItem -LiteralPath $r -Recurse -File -Force -ErrorAction SilentlyContinue); $s=($f | Measure-Object -Property Length -Sum).Sum; if($null -eq $s){$s=0}; $a=New-Object 'System.Collections.Generic.List[string]'; $a.Add('generated=' + (Get-Date -Format s)); $a.Add('files=' + $f.Count); $a.Add('bytes=' + [int64]$s); [IO.File]::WriteAllLines('%BACKUP_DEST%\files-manifest.txt', $a)"
if errorlevel 1 (
  echo BACKUP FAILED: file manifest generation failed
  exit /b 1
)
echo file manifest written
echo BACKUP OK
endlocal
