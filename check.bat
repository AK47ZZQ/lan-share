@echo off
REM M0 local quality gate (no git remote yet; portable to CI later).
REM 四段门禁：pytest 基线 + ruff 静态检查 + mypy 类型 + pip-audit 依赖漏洞。
REM 规则集见 pyproject.toml（风格类豁免说明在同处）；pip-audit 依赖网络，
REM 离线时该段会失败——可临时注释，其余三段必须绿。
setlocal
set PYTHON=C:\Users\<你的用户名>\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe

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
echo ALL CHECKS PASSED
