param([int]$Port = 8000, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Python -eq 'python' -and (Test-Path -LiteralPath "$env:LOCALAPPDATA/Programs/Thonny/python.exe")) {
    $Python = "$env:LOCALAPPDATA/Programs/Thonny/python.exe"
}
Write-Host "Set UPDATE_BASE_URL to http://YOUR-LAN-IP:$Port (serves build/manifest.json)."
& $Python tools/mirror.py --port $Port
