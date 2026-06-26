<#
    deskctrl — Windows installer (PowerShell)
    ==========================================
    Usage: iwr -useb https://github.com/surgeodev/deskctrl/releases/latest/download/install.ps1 | iex

    Or save and run locally:
        powershell -ExecutionPolicy Bypass -File install.ps1
#>

$ErrorActionPreference = "Stop"
$Repo = "surgeodev/deskctrl"
$ReleaseUrl = "https://github.com/$Repo/releases/latest/download"

# ── Colors ──────────────────────────────────────────────────────────
function Write-Info  { Write-Host ":: $args" -ForegroundColor Cyan }
function Write-Ok   { Write-Host "  ✓ $args" -ForegroundColor Green }
function Write-Warn { Write-Host "  ⚠ $args" -ForegroundColor Yellow }
function Write-Fail { Write-Host "  ✗ $args" -ForegroundColor Red }

function Show-Header {
    Write-Host "`n── $args ──`n" -ForegroundColor White
}

# ═══════════════════════════════════════════════════════════════════
# STEP 1: Check requirements
# ═══════════════════════════════════════════════════════════════════
Show-Header "Checking requirements"

# Check if running in PowerShell
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Fail "PowerShell 5+ required"
    exit 1
}
Write-Ok "PowerShell $($PSVersionTable.PSVersion)"

# Check for Python
$python = $null
foreach ($cmd in @("python3", "python")) {
    try {
        $v = & $cmd --version 2>&1
        if ($v -match "(\d+\.\d+)") {
            $python = $cmd
            break
        }
    } catch {}
}

if (-not $python) {
    Write-Fail "Python not found! Installing via winget..."
    try {
        winget install Python.Python.3.12
        $python = "python"
        Write-Ok "Python installed"
    } catch {
        Write-Fail "Could not install Python automatically."
        Write-Host "  Download from: https://www.python.org/downloads/"
        Write-Host "  Make sure to check 'Add Python to PATH' during installation."
        exit 1
    }
}

$pyVer = & $python --version 2>&1
Write-Ok "$pyVer found at $(Get-Command $python).Source"

# Check pip
try {
    & $python -m pip --version 2>&1 | Out-Null
    Write-Ok "pip available"
} catch {
    Write-Fail "pip not available"
    exit 1
}

# ═══════════════════════════════════════════════════════════════════
# STEP 2: Install
# ═══════════════════════════════════════════════════════════════════
Show-Header "Installing deskctrl"

# Check if installed via pip first (user already has pip)
$installArgs = @()
if ($args -contains "--no-gui") {
    $installArgs += "--no-deps"
    Write-Info "Skipping GUI dependencies"
}

# Try to download the .exe first (fast path)
$tempDir = "$env:TEMP\deskctrl-install"
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
$zipFile = "$tempDir\deskctrl-windows-x64.zip"
$exeFile = "$tempDir\deskctrl.exe"

Write-Info "Downloading deskctrl.exe..."
try {
    Invoke-WebRequest -Uri "$ReleaseUrl/deskctrl-windows-x64.zip" -OutFile $zipFile -ErrorAction Stop
    Expand-Archive -Path $zipFile -DestinationPath $tempDir -Force
    Write-Ok "deskctrl.exe downloaded"
    
    # Install to a good location
    $installDir = "$env:LOCALAPPDATA\deskctrl"
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    Move-Item -Force "$tempDir\deskctrl.exe" "$installDir\deskctrl.exe"
    
    # Add to PATH for current user
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -notlike "*$installDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$installDir", "User")
        $env:Path = "$env:Path;$installDir"
    }
    
    Write-Ok "deskctrl installed to $installDir\deskctrl.exe"
    Write-Host ""
    Write-Host "  Open a NEW PowerShell/CMD window, then run:"
    Write-Host "    deskctrl --help"
} catch {
    Write-Warn "Binary download failed ($($_.Exception.Message))"
    Write-Info "Falling back to pip install..."
    
    # Fall back to pip
    try {
        & $python -m pip install deskctrl 2>&1
        Write-Ok "deskctrl installed via pip"
    } catch {
        # Install from source
        $srcDir = "$tempDir\src"
        Write-Info "Installing from source..."
        & git clone --depth 1 "https://github.com/$Repo.git" $srcDir 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $python -m pip install -e "$srcDir"
            Write-Ok "deskctrl installed from source"
        } else {
            Write-Fail "Could not install deskctrl"
            exit 1
        }
    }
}

# Cleanup
Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue

# ═══════════════════════════════════════════════════════════════════
# Done
# ═══════════════════════════════════════════════════════════════════
Show-Header "Installation complete!"

Write-Host "  deskctrl is ready!" -ForegroundColor Green
Write-Host ""
Write-Host "  To use it, open a new terminal and run:"
Write-Host "    deskctrl --help"
Write-Host "    deskctrl serve"
Write-Host "    deskctrl connect <ip>"
Write-Host "    deskctrl monitor"
Write-Host ""
Write-Host "  Or double-click the .exe to launch the GUI:"
Write-Host "    $installDir\deskctrl.exe gui"
