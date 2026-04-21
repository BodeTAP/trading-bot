# =============================================================================
# Trading Bot -- Windows VPS Setup Script
# Jalankan sebagai Administrator di PowerShell:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\setup_vps_windows.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

$REPO_URL    = "https://github.com/BodeTAP/trading-bot.git"
$INSTALL_DIR = "C:\trading-bot"
$NSSM_URL    = "https://nssm.cc/release/nssm-2.24.zip"
$NSSM_DIR    = "C:\nssm"

function Write-Step { param($msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[!]  $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "[ERR] $msg" -ForegroundColor Red }

# =============================================================================
# 0. Cek Administrator
# =============================================================================
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")
if (-NOT $isAdmin) {
    Write-Err "Jalankan PowerShell sebagai Administrator!"
    exit 1
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "   Trading Bot -- Windows VPS Auto Setup        " -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""

# =============================================================================
# 1. Install Python via winget
# =============================================================================
Write-Step "Mengecek Python 3.11..."
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) {
    $ver = python --version 2>&1
    Write-Ok "Python sudah terinstall: $ver"
} else {
    Write-Step "Install Python 3.11 via winget..."
    winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
    Write-Ok "Python terinstall"
}

# =============================================================================
# 2. Install Git via winget
# =============================================================================
Write-Step "Mengecek Git..."
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd) {
    Write-Ok "Git sudah terinstall"
} else {
    Write-Step "Install Git via winget..."
    winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
    Write-Ok "Git terinstall"
}

# =============================================================================
# 3. Clone / Update repo
# =============================================================================
Write-Step "Setup repository di $INSTALL_DIR..."
if (Test-Path "$INSTALL_DIR\.git") {
    Write-Warn "Repo sudah ada -- pull update terbaru"
    Set-Location $INSTALL_DIR
    git pull origin master
} else {
    git clone $REPO_URL $INSTALL_DIR
    Set-Location $INSTALL_DIR
}
Write-Ok "Repository siap di $INSTALL_DIR"

# =============================================================================
# 4. Virtual environment & dependencies
# =============================================================================
Write-Step "Setup Python virtual environment..."
if (-NOT (Test-Path "$INSTALL_DIR\venv")) {
    python -m venv "$INSTALL_DIR\venv"
}
& "$INSTALL_DIR\venv\Scripts\python.exe" -m pip install --upgrade pip -q
& "$INSTALL_DIR\venv\Scripts\pip.exe" install -r "$INSTALL_DIR\requirements.txt" -q
Write-Ok "Dependencies terinstall"

# =============================================================================
# 5. Setup file .env
# =============================================================================
Write-Step "Konfigurasi file .env..."
$envFile = "$INSTALL_DIR\.env"

if (Test-Path $envFile) {
    Write-Warn ".env sudah ada -- lewati. Edit manual: notepad $envFile"
} else {
    Write-Host ""
    Write-Host "Masukkan konfigurasi API (Enter untuk melewati):" -ForegroundColor Yellow
    Write-Host ""

    $binanceKey    = Read-Host "  BINANCE_API_KEY"
    $binanceSecret = Read-Host "  BINANCE_SECRET_KEY"
    $anthropicKey  = Read-Host "  ANTHROPIC_API_KEY"
    $tgToken       = Read-Host "  TELEGRAM_BOT_TOKEN"
    $tgChatId      = Read-Host "  TELEGRAM_CHAT_ID"
    $tradingPair   = Read-Host "  TRADING_PAIR [ETH/USDT]"
    if (-not $tradingPair) { $tradingPair = "ETH/USDT" }

    $envContent = "BINANCE_API_KEY=$binanceKey`r`nBINANCE_SECRET_KEY=$binanceSecret`r`nANTHROPIC_API_KEY=$anthropicKey`r`nTRADING_PAIR=$tradingPair`r`nBINANCE_SANDBOX=false`r`nTIMEFRAME=1h`r`nMAX_DRAWDOWN_PCT=20`r`nTRAILING_STOP_ATR_MULTIPLIER=1.5`r`nTIMEFRAME_SHORT=1h`r`nTIMEFRAME_MEDIUM=4h`r`nTIMEFRAME_LONG=1d`r`nTELEGRAM_BOT_TOKEN=$tgToken`r`nTELEGRAM_CHAT_ID=$tgChatId`r`nMAX_POSITION_SIZE_PCT=20`r`nENSEMBLE_BUY_THRESHOLD=0.30`r`nENSEMBLE_SELL_THRESHOLD=-0.30`r`n"
    [System.IO.File]::WriteAllText($envFile, $envContent, [System.Text.Encoding]::UTF8)
    Write-Ok ".env dibuat"
}

# =============================================================================
# 6. Buat folder logs
# =============================================================================
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\logs" | Out-Null
Write-Ok "Folder logs siap"

# =============================================================================
# 7. Streamlit config
# =============================================================================
Write-Step "Setup Streamlit dashboard..."
$stDir = "$INSTALL_DIR\.streamlit"
New-Item -ItemType Directory -Force -Path $stDir | Out-Null

if (-NOT (Test-Path "$stDir\config.toml")) {
    $dashPass = Read-Host "  Password dashboard (default: trading123)"
    if (-not $dashPass) { $dashPass = "trading123" }

    $cfgContent = "[server]`r`nheadless = true`r`nport = 8501`r`naddress = `"0.0.0.0`"`r`n"
    [System.IO.File]::WriteAllText("$stDir\config.toml", $cfgContent, [System.Text.Encoding]::UTF8)

    $secContent = "password = `"$dashPass`"`r`n"
    [System.IO.File]::WriteAllText("$stDir\secrets.toml", $secContent, [System.Text.Encoding]::UTF8)

    Write-Ok "Streamlit dikonfigurasi (port 8501)"
} else {
    Write-Warn "Streamlit config sudah ada -- dilewati"
}

# =============================================================================
# 8. Install NSSM
# =============================================================================
Write-Step "Install NSSM (Windows Service Manager)..."

if (-NOT (Test-Path "$NSSM_DIR\nssm.exe")) {
    $zipPath = "$env:TEMP\nssm.zip"
    Write-Warn "Download NSSM..."
    Invoke-WebRequest -Uri $NSSM_URL -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath "$env:TEMP\nssm_extract" -Force
    New-Item -ItemType Directory -Force -Path $NSSM_DIR | Out-Null
    Copy-Item "$env:TEMP\nssm_extract\nssm-2.24\win64\nssm.exe" "$NSSM_DIR\nssm.exe"
    Remove-Item $zipPath -Force
    Remove-Item "$env:TEMP\nssm_extract" -Recurse -Force
    Write-Ok "NSSM terinstall di $NSSM_DIR"
} else {
    Write-Ok "NSSM sudah ada"
}

$nssmExe = "$NSSM_DIR\nssm.exe"

# =============================================================================
# 9. Daftarkan Bot sebagai Windows Service
# =============================================================================
Write-Step "Mendaftarkan Trading Bot sebagai Windows Service..."

$svc1 = Get-Service -Name "TradingBot" -ErrorAction SilentlyContinue
if ($svc1) {
    Write-Warn "Service TradingBot sudah ada -- hapus dan buat ulang"
    & $nssmExe stop TradingBot 2>$null
    & $nssmExe remove TradingBot confirm 2>$null
    Start-Sleep -Seconds 2
}

& $nssmExe install TradingBot "$INSTALL_DIR\venv\Scripts\python.exe"
& $nssmExe set TradingBot AppParameters "bot_runner.py"
& $nssmExe set TradingBot AppDirectory $INSTALL_DIR
& $nssmExe set TradingBot DisplayName "Trading Bot (bot_runner)"
& $nssmExe set TradingBot Description "Crypto Trading Bot dengan Claude AI"
& $nssmExe set TradingBot Start SERVICE_AUTO_START
& $nssmExe set TradingBot AppStdout "$INSTALL_DIR\logs\service_bot.log"
& $nssmExe set TradingBot AppStderr "$INSTALL_DIR\logs\service_bot_err.log"
& $nssmExe set TradingBot AppRotateFiles 1
& $nssmExe set TradingBot AppRotateBytes 10485760
Write-Ok "Service TradingBot terdaftar"

# =============================================================================
# 10. Daftarkan Dashboard sebagai Windows Service
# =============================================================================
Write-Step "Mendaftarkan Dashboard sebagai Windows Service..."

$svc2 = Get-Service -Name "TradingDashboard" -ErrorAction SilentlyContinue
if ($svc2) {
    Write-Warn "Service TradingDashboard sudah ada -- hapus dan buat ulang"
    & $nssmExe stop TradingDashboard 2>$null
    & $nssmExe remove TradingDashboard confirm 2>$null
    Start-Sleep -Seconds 2
}

& $nssmExe install TradingDashboard "$INSTALL_DIR\venv\Scripts\streamlit.exe"
& $nssmExe set TradingDashboard AppParameters "run dashboard.py --server.port 8501 --server.address 0.0.0.0 --server.headless true"
& $nssmExe set TradingDashboard AppDirectory $INSTALL_DIR
& $nssmExe set TradingDashboard DisplayName "Trading Bot Dashboard (Streamlit)"
& $nssmExe set TradingDashboard Description "Trading Bot Dashboard"
& $nssmExe set TradingDashboard Start SERVICE_AUTO_START
& $nssmExe set TradingDashboard AppStdout "$INSTALL_DIR\logs\service_dashboard.log"
& $nssmExe set TradingDashboard AppStderr "$INSTALL_DIR\logs\service_dashboard_err.log"
& $nssmExe set TradingDashboard AppRotateFiles 1
Write-Ok "Service TradingDashboard terdaftar"

# =============================================================================
# 11. Start services
# =============================================================================
Write-Step "Menjalankan services..."
& $nssmExe start TradingBot
Start-Sleep -Seconds 3
& $nssmExe start TradingDashboard
Write-Ok "Services berjalan"

# =============================================================================
# 12. Buka port 8501 di Windows Firewall
# =============================================================================
Write-Step "Membuka port 8501 di Windows Firewall..."
$fwRule = Get-NetFirewallRule -DisplayName "Trading Bot Dashboard" -ErrorAction SilentlyContinue
if (-not $fwRule) {
    New-NetFirewallRule -DisplayName "Trading Bot Dashboard" `
        -Direction Inbound -Protocol TCP -LocalPort 8501 -Action Allow | Out-Null
    Write-Ok "Port 8501 dibuka"
} else {
    Write-Ok "Port 8501 sudah terbuka"
}

# =============================================================================
# 13. Selesai
# =============================================================================
try {
    $serverIP = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 5).Content.Trim()
} catch {
    $serverIP = "IP_VPS_ANDA"
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "   Setup Selesai!                               " -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Bot       : " -NoNewline; Write-Host "Berjalan sebagai Windows Service" -ForegroundColor Green
Write-Host "  Dashboard : " -NoNewline; Write-Host "http://${serverIP}:8501" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Perintah berguna (jalankan sebagai Administrator):"
Write-Host "    Get-Service TradingBot"
Write-Host "    Restart-Service TradingBot"
Write-Host "    Get-Content $INSTALL_DIR\logs\bot.log -Tail 50 -Wait"
Write-Host ""
Write-Host "  Update kode dari GitHub:" -ForegroundColor Yellow
Write-Host "    cd $INSTALL_DIR"
Write-Host "    git pull origin master"
Write-Host "    Restart-Service TradingBot"
Write-Host "    Restart-Service TradingDashboard"
Write-Host ""
