@echo off
REM M0 local quality gate (no git remote yet; portable to CI later).
REM 六段门禁：pytest 基线 + ruff 静态检查 + mypy 类型 + pip-audit 依赖漏洞
REM   + UI 分层守卫（mobile/lib/ui 不得直连 dio；纯静态扫描，不需 Dart 工具链）
REM   + SQLite 专有语法守卫（业务层 SQL 出现 PG 不认的 SQLite 方言即红，v3.56 双引擎盲区）。
REM 规则集见 pyproject.toml（风格类豁免说明在同处）；pip-audit 依赖网络，
REM 离线时该段会失败——可临时注释，其余五段必须绿。
setlocal
set PYTHON=C:\lan-share\.venv\Scripts\python.exe

echo === pytest baseline ===
"%PYTHON%" -m pytest tests/ -q
if errorlevel 1 exit /b 1

echo.
echo === ruff ===
"%PYTHON%" -m ruff check app tests server.py
if errorlevel 1 exit /b 1

echo.
echo === mypy ===
"%PYTHON%" -m mypy app
if errorlevel 1 exit /b 1

echo.
echo === pip-audit ===
"%PYTHON%" -m pip_audit -l --skip-editable
if errorlevel 1 exit /b 1

echo.
echo === pip check (dependency conflicts; gate and runtime share this venv, so a
echo     conflict here means the production interpreter carries it too — 2026-09-21
echo     httpx2 silent rollback in the same venv is exactly the failure shape) ===
"%PYTHON%" -m pip check
if errorlevel 1 exit /b 1

echo.
echo === UI layering guard (mobile) ===
"%PYTHON%" scripts\check-ui-layering.py
if errorlevel 1 exit /b 1

echo.
echo === SQLite-isms guard (dual-engine SQL portability) ===
"%PYTHON%" scripts\check-sqlite-isms.py
if errorlevel 1 exit /b 1

echo.
echo ALL CHECKS PASSED
