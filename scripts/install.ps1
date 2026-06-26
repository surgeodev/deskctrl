<#
    deskctrl -- Windows installer
    ==============================
    One command:
        iwr -useb https://github.com/surgeodev/deskctrl/releases/latest/download/install.ps1 | iex

    After install, open a NEW terminal and run: deskctrl gui
#>

$ErrorActionPreference = "Stop"
$Repo = "surgeodev/deskctrl"
$ReleaseUrl = "https://github.com/$Repo/releases/latest/download"
$InstallDir = "$env:LOCALAPPDATA\deskctrl"

Write-Host "==> Installing deskctrl..." -ForegroundColor Cyan

# Create install directory
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Download the .exe
$exePath = "$InstallDir\deskctrl.exe"
try {
    # Try direct .exe download first (if we add it as release asset)
    Invoke-WebRequest -Uri "$ReleaseUrl/deskctrl.exe" -OutFile $exePath -ErrorAction Stop
} catch {
    # Fall back to zip download
    Write-Host "Downloading deskctrl-windows-x64.zip..." -ForegroundColor Cyan
    $zipPath = "$env:TEMP\deskctrl.zip"
    Invoke-WebRequest -Uri "$ReleaseUrl/deskctrl-windows-x64.zip" -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $InstallDir -Force
    Remove-Item $zipPath -Force
    # The .exe is inside the extracted folder
    $extractedExe = Get-ChildItem -Path $InstallDir -Recurse -Filter "deskctrl.exe" | Select-Object -First 1
    if ($extractedExe) {
        Move-Item -Force $extractedExe.FullName "$InstallDir\deskctrl.exe"
        # Clean up extracted folders
        Get-ChildItem -Path $InstallDir -Directory | Remove-Item -Recurse -Force
    }
}

# Add to PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$InstallDir", "User")
    $env:Path = "$env:Path;$InstallDir"
}

Write-Host ""
Write-Host "  done!" -ForegroundColor Green
Write-Host ""
Write-Host "  Open a NEW terminal (PowerShell or CMD) and run:"
Write-Host "    deskctrl gui"
Write-Host ""
Write-Host "  Or run directly now:"
Write-Host "    $InstallDir\deskctrl.exe gui"
