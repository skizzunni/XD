param(
    [string]$NinjaTraderHome = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'),
    [string]$RunId = 'forward-paper',
    [string]$PythonExe = 'python'
)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot
$folder = Join-Path $NinjaTraderHome (Join-Path 'MNQPaper' $RunId)
$output = Join-Path $folder 'dashboard.html'
$opened = $false
Push-Location $project
try {
    while ($true) {
        $logs = @(Get-ChildItem $folder -Filter '*_events.csv' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        if ($logs.Count -gt 0) {
            & $PythonExe main.py dashboard --nt-events @logs --out $output
            if ($LASTEXITCODE -ne 0) { Write-Warning 'Dashboard refresh failed; retrying on next snapshot.' }
            elseif (!$opened) { Start-Process $output; $opened = $true }
        } else { Write-Host "Waiting for NinjaTrader logs in $folder" }
        Start-Sleep -Seconds 5
    }
} finally { Pop-Location }
