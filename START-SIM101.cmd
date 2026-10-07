@echo off
setlocal
cd /d "%~dp0"
echo MNQ Sim101 setup and dashboard
echo Disable any existing MNQPlanPaper instance before updating its source.
echo Existing different strategy/calendar files will be backed up.
echo.
if not exist "windows_setup.py" goto incomplete
python windows_setup.py check-package --dashboard-only
if errorlevel 1 goto failed
python -m pip install --require-hashes --only-binary=:all: --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto dashboard
echo The launcher will ask for the Paper run ID shown in NinjaTrader.
echo Leave this window open. The dashboard opens even while waiting for native logs.
python windows_setup.py launch %*
if errorlevel 1 goto failed
exit /b 0
:dashboard
echo Dependency installation failed. Opening the read-only dashboard; strategy installation is skipped.
python windows_setup.py launch --dashboard-only %*
if errorlevel 1 goto failed
exit /b 0
:incomplete
echo Incomplete download. Choose Extract All on the complete ZIP and run this file inside the extracted folder.
goto failed
:failed
echo.
echo Setup stopped. Read the error above; the bot has not been enabled by this launcher.
pause
exit /b 1
