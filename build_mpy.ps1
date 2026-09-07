param([string]$Version = '2.0.0-rebuild.5', [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Python -eq 'python' -and (Test-Path -LiteralPath "$env:LOCALAPPDATA/Programs/Thonny/python.exe")) {
    $Python = "$env:LOCALAPPDATA/Programs/Thonny/python.exe"
}
& $Python tools/build.py --version $Version
if ($LASTEXITCODE -ne 0) { throw 'Firmware build failed' }
