param(
    [string]$NinjaTraderHome = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'),
    [string]$CalendarPath
)
$ErrorActionPreference = 'Stop'
$destination = Join-Path $NinjaTraderHome 'bin\Custom\Strategies\MNQPlanPaper.cs'
if (!(Test-Path (Split-Path $destination))) { throw 'Install NinjaTrader 8 before running this helper.' }
$source = Join-Path $PSScriptRoot 'MNQPlanPaper.cs'
if (Test-Path $destination) {
    if ((Get-FileHash $destination).Hash -ne (Get-FileHash $source).Hash) {
        throw 'MNQPlanPaper.cs already exists with different contents. Back it up and reconcile it before installing.'
    }
} else { Copy-Item $source $destination }
if ($CalendarPath) {
    $calendarDestination = Join-Path $NinjaTraderHome 'MNQCalendar.csv'
    if (Test-Path $calendarDestination) { throw 'Existing calendar preserved. Set the strategy CalendarFile to your new frozen file.' }
    Copy-Item $CalendarPath $calendarDestination
}
Write-Host "Strategy installed at $destination"
Write-Host 'Open NinjaScript Editor and press F5. Compile/playback validation is still required.'
