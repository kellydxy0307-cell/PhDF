@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw -m phdfloating.main
) else (
  python -m phdfloating.main
)
