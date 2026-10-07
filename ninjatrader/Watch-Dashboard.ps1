param(
    [string]$NinjaTraderHome = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'),
    [string]$RunId = 'r1-sim101-001',
    [string]$PythonExe = 'python'
)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot
Push-Location $project
try {
    & $PythonExe windows_setup.py --ninjatrader-home $NinjaTraderHome dashboard --run-id $RunId
    if ($LASTEXITCODE -ne 0) { throw 'Browser dashboard failed; inspect the error above.' }
} finally { Pop-Location }
