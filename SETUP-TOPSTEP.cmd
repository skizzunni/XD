@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python Launcher was not found. Install Python 3.12 or newer from python.org.
  pause
  exit /b 1
)
py -3 -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12 or newer is required'"
if errorlevel 1 (
  echo Python 3.12 or newer is required.
  pause
  exit /b 1
)
if not exist ".topstep-venv\Scripts\python.exe" py -3 -m venv .topstep-venv
if errorlevel 1 (
  echo Could not create the local Topstep environment.
  pause
  exit /b 1
)
.topstep-venv\Scripts\python.exe -m unittest tests.test_topstep -q
if errorlevel 1 (
  echo Topstep safety checks failed. Do not connect this build.
  pause
  exit /b 1
)
echo Topstep read-only tools are ready. Order writes remain disabled.
pause
exit /b 0
