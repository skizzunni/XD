@echo off
setlocal
cd /d "%~dp0"
echo MNQ browser dashboard - reads existing NinjaTrader logs.
echo This launcher does not replace or enable your strategy.
if not exist "windows_setup.py" goto incomplete
python windows_setup.py check-package
if errorlevel 1 goto failed
python -m pip install --require-hashes --only-binary=:all: --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
python windows_setup.py launch --dashboard-only %*
if errorlevel 1 goto failed
exit /b 0
:incomplete
echo Choose Extract All on the complete ZIP, then run this file inside the extracted folder.
:failed
echo Dashboard startup stopped. Read the error above.
pause
exit /b 1
