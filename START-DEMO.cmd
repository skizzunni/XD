@echo off
setlocal
cd /d "%~dp0"
echo Standalone paper bot DEMO - synthetic prices, no API key or paid data required.
if not exist "standalone_paper.py" goto failed
python -m pip install --require-hashes --only-binary=:all: --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
python standalone_paper.py --demo %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Demo setup stopped. Read the error above and use a complete extracted package.
pause
exit /b 1
