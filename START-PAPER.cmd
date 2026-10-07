@echo off
setlocal
cd /d "%~dp0"
echo Standalone MNQ paper bot with Databento data and browser dashboard
echo Keep your Databento API key on this computer. It will be requested with hidden input.
echo Review and activate real-time CME GLBX.MDP3 access with Databento first.
echo.
if not exist "standalone_paper.py" goto incomplete
python -m venv .live-venv
if errorlevel 1 goto failed
.live-venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: --disable-pip-version-check -r requirements-live.txt
if errorlevel 1 goto failed
echo Historical downloads are separately metered. Enter a maximum estimated dollar amount for this launch.
echo Enter 0 to use existing cached history only. For a first launch, review provider costs and enter your chosen cap.
set "MNQ_HISTORY_BUDGET=0"
set /p "MNQ_HISTORY_BUDGET=Historical download cap in dollars [0]: "
set "MNQ_PAPER_STAGE=funded"
set /p "MNQ_PAPER_STAGE=Paper account stage: funded or evaluation [funded]: "
.live-venv\Scripts\python.exe standalone_paper.py %*
if errorlevel 1 goto failed
exit /b 0
:incomplete
echo Download the complete standalone ZIP and choose Extract All before running this launcher.
:failed
echo Setup stopped. Read the error above. Your saved paper records are preserved.
pause
exit /b 1
