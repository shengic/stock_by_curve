param(
    [ValidateSet("us", "tw", "both")]
    [string]$Target = "both",
    [switch]$Clean,
    [string]$VenvPath = ".venv-build",
    [string]$PythonCmd = "python"
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [string]$Message,
        [scriptblock]$Action
    )
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
    & $Action
}

function Get-VenvPython {
    param([string]$BasePath)
    $py = Join-Path $BasePath "Scripts\python.exe"
    if (-not (Test-Path $py)) {
        throw "Virtual environment python not found: $py"
    }
    return $py
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if ($Clean) {
    Invoke-Step "Cleaning build/, dist/, *.spec" {
        if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
        if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }
        Get-ChildItem -Filter "*.spec" | Remove-Item -Force
    }
}

if (-not (Test-Path $VenvPath)) {
    Invoke-Step "Creating virtual environment at $VenvPath" {
        & $PythonCmd -m venv $VenvPath
    }
}

$VenvPython = Get-VenvPython -BasePath $VenvPath

Invoke-Step "Upgrading pip and installing dependencies" {
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install pyinstaller playwright pillow
}

Invoke-Step "Installing Playwright Chromium runtime" {
    # Force browsers into playwright/driver/package/.local-browsers so PyInstaller can bundle them.
    $env:PLAYWRIGHT_BROWSERS_PATH = "0"
    & $VenvPython -m playwright install chromium
}

function Build-App {
    param(
        [string]$Name,
        [string]$Entry
    )
    Invoke-Step "Building $Name from $Entry" {
        & $VenvPython -m PyInstaller `
            --noconfirm `
            --clean `
            --windowed `
            --name $Name `
            --collect-all playwright `
            --hidden-import pyee `
            $Entry
    }
}

if ($Target -eq "us" -or $Target -eq "both") {
    Build-App -Name "StockCurve_US" -Entry "tk_app.py"
}

if ($Target -eq "tw" -or $Target -eq "both") {
    Build-App -Name "StockCurve_TW" -Entry "tk_app_tw.py"
}

Invoke-Step "Copying default ticker files next to each exe" {
    if (Test-Path "dist\StockCurve_US") {
        Copy-Item "stock.txt" "dist\StockCurve_US\stock.txt" -Force
        Copy-Item "USER_GUIDE.txt" "dist\StockCurve_US\USER_GUIDE.txt" -Force
    }
    if (Test-Path "dist\StockCurve_TW") {
        Copy-Item "tw_stock.txt" "dist\StockCurve_TW\tw_stock.txt" -Force
        Copy-Item "USER_GUIDE_TW.txt" "dist\StockCurve_TW\USER_GUIDE.txt" -Force
    }
}

Write-Host ""
Write-Host "Build completed." -ForegroundColor Green
if (Test-Path "dist\StockCurve_US\StockCurve_US.exe") {
    Write-Host "US exe: dist\StockCurve_US\StockCurve_US.exe"
}
if (Test-Path "dist\StockCurve_TW\StockCurve_TW.exe") {
    Write-Host "TW exe: dist\StockCurve_TW\StockCurve_TW.exe"
}
