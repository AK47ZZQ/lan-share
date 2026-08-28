@echo off
REM M0 local quality gate (no git remote yet; portable to CI later).
REM TODO(M0): add ruff / mypy / pip-audit stages (see PLAN-v3).
setlocal
set PYTHON=C:\Users\<你的用户名>\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe

echo === pytest baseline ===
"%PYTHON%" -m pytest tests/ -q
if errorlevel 1 exit /b 1

echo.
echo ALL CHECKS PASSED
