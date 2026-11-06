param(
    [string]$PL9Exe = 'C:\GeoVision\PL9\PL9.exe'
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PL9Exe -PathType Leaf)) {
    throw "PL9.exe not found: $PL9Exe. Pass -PL9Exe with the installed path."
}
$python = Get-Command py.exe -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python Launcher py.exe is not installed.' }
$env:PL9_EXE = (Resolve-Path -LiteralPath $PL9Exe).Path
& py -3 -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pywinauto installation failed.' }
& py -3 (Join-Path $PSScriptRoot 'pl9_api.py')
exit $LASTEXITCODE
