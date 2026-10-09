@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0topstep\Start-Topstep-ReadOnly.ps1"
if errorlevel 1 (
  echo Read-only Topstep check failed. No order endpoint was enabled by this launcher.
  pause
  exit /b 1
)
pause
exit /b 0
