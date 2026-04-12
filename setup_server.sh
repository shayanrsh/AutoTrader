#!/bin/bash
# ============================================================================
# AutoTrader — Ubuntu 24.04 LTS Server Setup Script
# ============================================================================
# Run as root or with sudo:  sudo bash setup_server.sh
#
# This script:
# 1. Updates system packages
# 2. Installs Wine (to run MetaTrader 5)
# 3. Installs Xvfb (virtual display for headless Wine)
# 4. Installs Python 3.11+
# 5. Creates a dedicated 'trader' user
# 6. Sets up Wine + MT5 + Windows Python
# 7. Creates Python virtual environment for the bot
# 8. Configures firewall and SSH hardening
# 9. Installs systemd services
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[SETUP]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Check root ──────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "This script must be run as root (sudo bash setup_server.sh)"
    exit 1
fi

TRADER_USER="trader"
INSTALL_DIR="/home/${TRADER_USER}/autotrader"
WINE_PREFIX="/home/${TRADER_USER}/.wine"

# ============================================================================
# STEP 1: System Update & Base Packages
# ============================================================================
log "Step 1: Updating system packages..."
apt update && apt upgrade -y
apt install -y \
    software-properties-common \
    wget \
    curl \
    git \
    unzip \
    htop \
    tmux \
    ca-certificates \
    gnupg \
    lsb-release

# ============================================================================
# STEP 2: Install Python 3.11+
# ============================================================================
log "Step 2: Installing Python 3.11..."
apt install -y python3 python3-pip python3-venv python3-dev

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
log "Python version: ${PYTHON_VERSION}"

# ============================================================================
# STEP 3: Install Wine
# ============================================================================
log "Step 3: Installing Wine..."

# Enable 32-bit architecture (required for some Wine components)
dpkg --add-architecture i386
apt update

# Install Wine
apt install -y wine wine64 wine32 winbind

WINE_VERSION=$(wine --version 2>/dev/null || echo "unknown")
log "Wine version: ${WINE_VERSION}"

# ============================================================================
# STEP 4: Install Xvfb (Virtual Display)
# ============================================================================
log "Step 4: Installing Xvfb and X11 utilities..."
apt install -y xvfb x11-utils xauth

# ============================================================================
# STEP 5: Create Trader User
# ============================================================================
log "Step 5: Creating '${TRADER_USER}' user..."

if id "${TRADER_USER}" &>/dev/null; then
    log "User '${TRADER_USER}' already exists"
else
    adduser --disabled-password --gecos "" "${TRADER_USER}"
    log "User '${TRADER_USER}' created"
fi

# ============================================================================
# STEP 6: Setup Wine Environment & Install MT5
# ============================================================================
log "Step 6: Setting up Wine environment..."

# Initialize Wine prefix as the trader user
sudo -u "${TRADER_USER}" bash -c "
    export WINEPREFIX=${WINE_PREFIX}
    export WINEARCH=win64
    
    # Initialize Wine (auto-accept Mono and Gecko)
    wineboot --init 2>/dev/null || true
    sleep 5
    
    echo 'Wine prefix initialized at ${WINE_PREFIX}'
"

log "Downloading MetaTrader 5 installer..."
MT5_INSTALLER="/tmp/mt5setup.exe"
if [ ! -f "${MT5_INSTALLER}" ]; then
    wget -q -O "${MT5_INSTALLER}" \
        "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
fi

log "Installing MetaTrader 5 (headless via Xvfb)..."
sudo -u "${TRADER_USER}" bash -c "
    export WINEPREFIX=${WINE_PREFIX}
    export DISPLAY=:99
    
    # Start virtual display
    Xvfb :99 -screen 0 1024x768x24 &
    XVFB_PID=\$!
    sleep 2
    
    # Run MT5 installer (silent-ish, will auto-close after install)
    wine ${MT5_INSTALLER} /auto &
    sleep 30
    
    # Kill installer and Xvfb
    killall mt5setup.exe 2>/dev/null || true
    kill \$XVFB_PID 2>/dev/null || true
    
    echo 'MT5 installation attempt completed'
"

# ============================================================================
# STEP 7: Install Windows Python in Wine
# ============================================================================
log "Step 7: Installing Windows Python in Wine..."

WIN_PYTHON_VERSION="3.11.9"
WIN_PYTHON_INSTALLER="/tmp/python-${WIN_PYTHON_VERSION}-amd64.exe"

if [ ! -f "${WIN_PYTHON_INSTALLER}" ]; then
    wget -q -O "${WIN_PYTHON_INSTALLER}" \
        "https://www.python.org/ftp/python/${WIN_PYTHON_VERSION}/python-${WIN_PYTHON_VERSION}-amd64.exe"
fi

sudo -u "${TRADER_USER}" bash -c "
    export WINEPREFIX=${WINE_PREFIX}
    export DISPLAY=:99
    
    Xvfb :99 -screen 0 1024x768x24 &
    XVFB_PID=\$!
    sleep 2
    
    # Install Python silently
    wine ${WIN_PYTHON_INSTALLER} /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 2>/dev/null
    sleep 20
    
    kill \$XVFB_PID 2>/dev/null || true
    
    echo 'Windows Python installation completed'
"

log "Installing MT5 Python packages in Wine Python..."
sudo -u "${TRADER_USER}" bash -c "
    export WINEPREFIX=${WINE_PREFIX}
    
    # Install MetaTrader5 and mt5linux in Wine Python
    wine python -m pip install --upgrade pip 2>/dev/null
    wine python -m pip install MetaTrader5 mt5linux 2>/dev/null
    
    echo 'Wine Python packages installed'
"

# ============================================================================
# STEP 8: Setup Bot Python Environment
# ============================================================================
log "Step 8: Setting up bot Python virtual environment..."

mkdir -p "${INSTALL_DIR}"
chown -R "${TRADER_USER}:${TRADER_USER}" "${INSTALL_DIR}"

sudo -u "${TRADER_USER}" bash -c "
    cd ${INSTALL_DIR}
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    
    # Install bot dependencies (if requirements.txt exists)
    if [ -f requirements.txt ]; then
        pip install -r requirements.txt
    fi
    
    echo 'Bot virtual environment created at ${INSTALL_DIR}/venv'
"

# Create data directory
mkdir -p "${INSTALL_DIR}/data"
chown -R "${TRADER_USER}:${TRADER_USER}" "${INSTALL_DIR}/data"

# ============================================================================
# STEP 9: Firewall Configuration (UFW)
# ============================================================================
log "Step 9: Configuring firewall..."

apt install -y ufw

# Default policies
ufw default deny incoming
ufw default allow outgoing

# Allow SSH (use your custom port if changed)
ufw allow 22/tcp comment "SSH"

# Allow health check endpoint (optional, from specific IPs only)
# ufw allow from YOUR_MONITOR_IP to any port 8080 comment "Health Check"

# Enable firewall
ufw --force enable
ufw status verbose

log "Firewall configured: deny incoming, allow SSH + outbound"

# ============================================================================
# STEP 10: SSH Hardening
# ============================================================================
log "Step 10: Hardening SSH..."

SSHD_CONFIG="/etc/ssh/sshd_config"

# Backup
cp "${SSHD_CONFIG}" "${SSHD_CONFIG}.bak"

# Apply hardening (only if not already set)
configure_ssh() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}" "${SSHD_CONFIG}"; then
        sed -i "s/^${key}.*/${key} ${value}/" "${SSHD_CONFIG}"
    else
        echo "${key} ${value}" >> "${SSHD_CONFIG}"
    fi
}

configure_ssh "PermitRootLogin" "no"
configure_ssh "PasswordAuthentication" "yes"  # Change to "no" after setting up SSH keys
configure_ssh "MaxAuthTries" "3"
configure_ssh "ClientAliveInterval" "300"
configure_ssh "ClientAliveCountMax" "2"
configure_ssh "X11Forwarding" "no"
configure_ssh "AllowAgentForwarding" "no"
configure_ssh "Protocol" "2"

# Restart SSH
systemctl restart sshd

log "SSH hardened (root login disabled, max 3 auth tries)"
warn "After setting up SSH keys, change PasswordAuthentication to 'no'"

# ============================================================================
# STEP 11: Install fail2ban
# ============================================================================
log "Step 11: Installing fail2ban..."

apt install -y fail2ban

cat > /etc/fail2ban/jail.local << 'EOF'
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 5
bantime = 3600
findtime = 600
EOF

systemctl enable fail2ban
systemctl restart fail2ban

log "fail2ban configured: 5 failed attempts = 1 hour ban"

# ============================================================================
# STEP 12: Install Systemd Services
# ============================================================================
log "Step 12: Installing systemd services..."

# Copy service files
if [ -d "${INSTALL_DIR}/systemd" ]; then
    cp "${INSTALL_DIR}/systemd/mt5-bridge.service" /etc/systemd/system/
    cp "${INSTALL_DIR}/systemd/autotrader.service" /etc/systemd/system/

    # Make bridge script executable
    chmod +x "${INSTALL_DIR}/systemd/start_mt5_bridge.sh"

    systemctl daemon-reload
    systemctl enable mt5-bridge.service
    systemctl enable autotrader.service

    log "Systemd services installed and enabled"
    warn "Start with: sudo systemctl start mt5-bridge && sudo systemctl start autotrader"
else
    warn "Systemd directory not found at ${INSTALL_DIR}/systemd — skipping service install"
fi

# ============================================================================
# STEP 13: Create Log Directory
# ============================================================================
log "Step 13: Setting up log directory..."

mkdir -p /var/log/autotrader
chown "${TRADER_USER}:${TRADER_USER}" /var/log/autotrader

# ============================================================================
# DONE
# ============================================================================
echo ""
echo "============================================================================"
echo -e "${GREEN}  Server Setup Complete!${NC}"
echo "============================================================================"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Copy your AutoTrader code to ${INSTALL_DIR}/"
echo "  2. Copy config.env.example to config.env and fill in your values:"
echo "     cp ${INSTALL_DIR}/config.env.example ${INSTALL_DIR}/config.env"
echo "     chmod 600 ${INSTALL_DIR}/config.env"
echo ""
echo "  3. First-time MT5 login (need VNC for GUI):"
echo "     Install a lightweight VNC server for initial login:"
echo "       apt install -y tigervnc-standalone-server"
echo "       sudo -u ${TRADER_USER} vncserver :1"
echo "     Connect via VNC, open MT5, login to Alpari, then close VNC."
echo ""
echo "  4. First-time Telegram auth (interactive):"
echo "     sudo -u ${TRADER_USER} bash -c 'cd ${INSTALL_DIR} && source venv/bin/activate && python -m src.main'"
echo "     Enter the verification code when prompted."
echo ""
echo "  5. Start the services:"
echo "     sudo systemctl start mt5-bridge"
echo "     sudo systemctl start autotrader"
echo ""
echo "  6. Check logs:"
echo "     journalctl -u autotrader -f"
echo "     journalctl -u mt5-bridge -f"
echo ""
echo "============================================================================"
