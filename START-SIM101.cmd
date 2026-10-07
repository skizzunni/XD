@echo off
setlocal
cd /d "%~dp0"
echo MNQ Sim101 setup and dashboard
echo Disable any existing MNQPlanPaper instance before updating its source.
echo Existing different strategy/calendar files will be backed up.
echo.
python -m pip install --require-hashes --only-binary=:all: --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
python windows_setup.py install
if errorlevel 1 goto failed
echo.
echo Next: compile in NinjaTrader with F5, connect live market data, and configure Sim101.
echo Read ninjatrader\SIM101-START-HERE.md for the exact settings.
echo Keep this window open for the dashboard.
python windows_setup.py dashboard --run-id p0-sim101-001
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Setup stopped. Read the error above; the bot has not been enabled by this launcher.
pause
exit /b 1
