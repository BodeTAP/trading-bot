#!/bin/bash
# =============================================================================
# Trading Bot — VPS Setup Script
# Tested on Ubuntu 22.04 LTS
# Usage: bash setup_vps.sh
# =============================================================================

set -e  # exit on error

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

REPO_URL="https://github.com/BodeTAP/trading-bot.git"
INSTALL_DIR="/root/trading-bot"
PYTHON="python3.11"
VENV="$INSTALL_DIR/venv"

print_step() { echo -e "\n${BLUE}▶ $1${NC}"; }
print_ok()   { echo -e "${GREEN}✓ $1${NC}"; }
print_warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_err()  { echo -e "${RED}✗ $1${NC}"; }

# =============================================================================
# 0. Root check
# =============================================================================
if [ "$EUID" -ne 0 ]; then
    print_err "Jalankan script ini sebagai root: sudo bash setup_vps.sh"
    exit 1
fi

echo -e "${GREEN}"
echo "╔══════════════════════════════════════════════════╗"
echo "║       Trading Bot — VPS Auto Setup               ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

# =============================================================================
# 1. Update sistem
# =============================================================================
print_step "Update sistem..."
apt update -qq && apt upgrade -y -qq
print_ok "Sistem diperbarui"

# =============================================================================
# 2. Install dependencies
# =============================================================================
print_step "Install Python 3.11, git, dan tools..."
apt install -y -qq python3.11 python3.11-venv python3-pip git curl ufw
print_ok "Dependencies terinstall"

# =============================================================================
# 3. Clone repo
# =============================================================================
print_step "Clone repository dari GitHub..."
if [ -d "$INSTALL_DIR" ]; then
    print_warn "Direktori $INSTALL_DIR sudah ada — pull update terbaru"
    cd "$INSTALL_DIR" && git pull origin master
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
print_ok "Repository siap di $INSTALL_DIR"

# =============================================================================
# 4. Virtual environment & dependencies
# =============================================================================
print_step "Setup Python virtual environment..."
cd "$INSTALL_DIR"
$PYTHON -m venv venv
source "$VENV/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q
print_ok "Python dependencies terinstall"

# =============================================================================
# 5. Setup file .env
# =============================================================================
print_step "Konfigurasi file .env..."

if [ -f "$INSTALL_DIR/.env" ]; then
    print_warn ".env sudah ada — lewati pengisian ulang"
    echo "   Untuk edit manual: nano $INSTALL_DIR/.env"
else
    echo ""
    echo "Masukkan konfigurasi API (tekan Enter untuk melewati):"
    echo ""

    read -rp "  BINANCE_API_KEY       : " BINANCE_API_KEY
    read -rp "  BINANCE_SECRET_KEY    : " BINANCE_SECRET_KEY
    read -rp "  ANTHROPIC_API_KEY     : " ANTHROPIC_API_KEY
    read -rp "  TELEGRAM_BOT_TOKEN    : " TELEGRAM_BOT_TOKEN
    read -rp "  TELEGRAM_CHAT_ID      : " TELEGRAM_CHAT_ID
    read -rp "  TRADING_PAIR [ETH/USDT]: " TRADING_PAIR
    TRADING_PAIR="${TRADING_PAIR:-ETH/USDT}"

    cat > "$INSTALL_DIR/.env" <<EOF
BINANCE_API_KEY=${BINANCE_API_KEY}
BINANCE_SECRET_KEY=${BINANCE_SECRET_KEY}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
TRADING_PAIR=${TRADING_PAIR}
BINANCE_SANDBOX=false
TIMEFRAME=1h
MAX_DRAWDOWN_PCT=20
TRAILING_STOP_ATR_MULTIPLIER=1.5
TIMEFRAME_SHORT=1h
TIMEFRAME_MEDIUM=4h
TIMEFRAME_LONG=1d
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
MAX_POSITION_SIZE_PCT=20
ENSEMBLE_BUY_THRESHOLD=0.30
ENSEMBLE_SELL_THRESHOLD=-0.30
EOF

    chmod 600 "$INSTALL_DIR/.env"
    print_ok ".env dibuat (hak akses 600 — hanya root yang bisa baca)"
fi

# =============================================================================
# 6. Buat folder logs
# =============================================================================
mkdir -p "$INSTALL_DIR/logs"
print_ok "Folder logs siap"

# =============================================================================
# 7. Streamlit config (password dashboard)
# =============================================================================
print_step "Setup password dashboard Streamlit..."
mkdir -p "$INSTALL_DIR/.streamlit"

if [ ! -f "$INSTALL_DIR/.streamlit/config.toml" ]; then
    read -rp "  Password untuk dashboard (default: trading123): " DASH_PASS
    DASH_PASS="${DASH_PASS:-trading123}"

    cat > "$INSTALL_DIR/.streamlit/config.toml" <<EOF
[server]
headless = true
port = 8501
address = "0.0.0.0"
EOF

    cat > "$INSTALL_DIR/.streamlit/secrets.toml" <<EOF
password = "${DASH_PASS}"
EOF
    chmod 600 "$INSTALL_DIR/.streamlit/secrets.toml"
    print_ok "Streamlit dikonfigurasi (port 8501, password diset)"
else
    print_warn ".streamlit/config.toml sudah ada — dilewati"
fi

# =============================================================================
# 8. Systemd service — Trading Bot
# =============================================================================
print_step "Membuat systemd service untuk bot..."

cat > /etc/systemd/system/trading-bot.service <<EOF
[Unit]
Description=Trading Bot (bot_runner.py)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV}/bin/python bot_runner.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

print_ok "Service trading-bot.service dibuat"

# =============================================================================
# 9. Systemd service — Dashboard
# =============================================================================
print_step "Membuat systemd service untuk dashboard..."

cat > /etc/systemd/system/trading-dashboard.service <<EOF
[Unit]
Description=Trading Bot Dashboard (Streamlit)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV}/bin/streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

print_ok "Service trading-dashboard.service dibuat"

# =============================================================================
# 10. Aktifkan & jalankan services
# =============================================================================
print_step "Aktifkan dan jalankan services..."
systemctl daemon-reload
systemctl enable trading-bot trading-dashboard
systemctl start trading-bot trading-dashboard
print_ok "Services berjalan"

# =============================================================================
# 11. Firewall
# =============================================================================
print_step "Setup firewall (UFW)..."
ufw --force reset -qq
ufw default deny incoming -q
ufw default allow outgoing -q
ufw allow 22/tcp    -q  # SSH
ufw allow 8501/tcp  -q  # Dashboard
ufw --force enable -q
print_ok "Firewall aktif (port 22 + 8501 terbuka)"

# =============================================================================
# 12. Selesai
# =============================================================================
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "IP_VPS")

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗"
echo    "║            Setup Selesai!                        ║"
echo -e "╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  🤖 Bot        : ${GREEN}Berjalan${NC} (systemctl status trading-bot)"
echo -e "  📊 Dashboard  : ${GREEN}http://${SERVER_IP}:8501${NC}"
echo ""
echo "  Perintah berguna:"
echo "    systemctl status trading-bot          # cek status bot"
echo "    systemctl restart trading-bot         # restart bot"
echo "    journalctl -u trading-bot -f          # log live bot"
echo "    journalctl -u trading-dashboard -f    # log live dashboard"
echo "    tail -f ${INSTALL_DIR}/logs/bot.log   # log file bot"
echo ""
echo -e "  ${YELLOW}Untuk update kode dari GitHub:${NC}"
echo "    cd ${INSTALL_DIR} && git pull origin master"
echo "    systemctl restart trading-bot trading-dashboard"
echo ""
