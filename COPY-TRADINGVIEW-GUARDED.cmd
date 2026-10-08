@echo off
setlocal
set "MNQ_PINE_FILE=%~dp0tradingview\MNQ_R2_Guarded.pine"
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; $file=$env:MNQ_PINE_FILE; if (-not (Test-Path -LiteralPath $file)) { throw 'Extract All the complete ZIP first. MNQ_R2_Guarded.pine is missing.' }; $source=Get-Content -LiteralPath $file -Raw -Encoding UTF8; if (-not $source.StartsWith('//@version=6')) { throw 'Unexpected strategy file format.' }; Set-Clipboard -Value $source; Write-Host 'Guarded paper-test script copied. Create a separate Pine strategy, Ctrl+A, Ctrl+V, Save, Add to chart. Keep the baseline report.'"
if errorlevel 1 (
  echo Copy failed. Open tradingview\MNQ_R2_Guarded.pine in Notepad and copy its text.
  pause
  exit /b 1
)
pause
exit /b 0
