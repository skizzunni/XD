$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.topstep-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run SETUP-TOPSTEP.cmd first; .topstep-venv\Scripts\python.exe is missing.'
}
$username = Read-Host 'TopstepX login username (not email or account name)'
$secureKey = Read-Host 'TopstepX API key (input is hidden)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:TOPSTEP_USERNAME = $username
    $env:TOPSTEP_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    & $python (Join-Path $root 'scripts\topstep_collect.py') --data-dir (Join-Path $root 'data\topstep')
    if ($LASTEXITCODE -ne 0) { throw "Collector failed with exit code $LASTEXITCODE" }
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    Remove-Item Env:TOPSTEP_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:TOPSTEP_USERNAME -ErrorAction SilentlyContinue
}
