@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0topstep\Start-Topstep-Collector.ps1"
if errorlevel 1 (
  echo Topstep collector stopped with an error. It does not contain order-writing code.
  pause
  exit /b 1
)
pause
exit /b 0
