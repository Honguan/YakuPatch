# Build dist\YakuPatch_win.zip (PROJECT_PLAN 12.1 A) from this checkout with the same steps as install.ps1.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_win.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot "build_typetree.ps1")
$app = Join-Path $root "build\YakuPatch"
if (Test-Path $app) { Remove-Item $app -Recurse -Force }
$env:YAKUPATCH_DIR = $app
$env:YAKUPATCH_SOURCE = $root
$env:YAKUPATCH_NOLAUNCH = "1"
& (Join-Path $PSScriptRoot "install.ps1")
New-Item -ItemType Directory -Force (Join-Path $root "dist") | Out-Null
$zip = Join-Path $root "dist\YakuPatch_win.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive $app $zip
Write-Host "Built $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
