#!/bin/bash
# ============================================================================
#
#     █████╗ ██╗   ██╗████████╗ ██████╗ ████████╗██████╗  █████╗ ██████╗ ███████╗██████╗
#    ██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗
#    ███████║██║   ██║   ██║   ██║   ██║   ██║   ██████╔╝███████║██║  ██║█████╗  ██████╔╝
#    ██╔══██║██║   ██║   ██║   ██║   ██║   ██║   ██╔══██╗██╔══██║██║  ██║██╔══╝  ██╔══██╗
#    ██║  ██║╚██████╔╝   ██║   ╚██████╔╝   ██║   ██║  ██║██║  ██║██████╔╝███████╗██║  ██║
#    ╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═╝
#
#  Automated XAUUSD Forex Trading System
#  One-line installer for Ubuntu 24.04 LTS
#
#  Usage:
#    bash <(curl -Ls https://raw.githubusercontent.com/shayanrsh/AutoTrader/main/install.sh)
#
# ============================================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Configuration ───────────────────────────────────────────────────────────
REPO_URL="https://github.com/shayanrsh/AutoTrader.git"
TRADER_USER="trader"
INSTALL_DIR="/home/${TRADER_USER}/autotrader"
WINE_PREFIX="/home/${TRADER_USER}/.wine"
MT5_INSTALLER_URL="https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
WIN_PYTHON_VERSION="3.11.9"
WIN_PYTHON_URL="https://www.python.org/ftp/python/${WIN_PYTHON_VERSION}/python-${WIN_PYTHON_VERSION}-amd64.exe"

# ── Banner ──────────────────────────────────────────────────────────────────
banner() {
    echo ""
    echo -e "${CYAN}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════════════════╗"
    echo "  ║                                                              ║"
    echo "  ║          🤖  AutoTrader — One-Line Installer  🤖            ║"
    echo "  ║                                                              ║"
    echo "  ║   Automated XAUUSD Forex Trading System                     ║"
    echo "  ║   Telegram Signals → AI Parsing → MT5 Execution            ║"
    echo "  ║                                                              ║"
    echo "  ╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
info() { echo -e "${BLUE}[i]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
step() { echo -e "\n${MAGENTA}${BOLD}━━━ Step $1: $2 ━━━${NC}"; }

# ── Preflight Checks ───────────────────────────────────────────────────────
preflight() {
    # Must be root
    if [[ $EUID -ne 0 ]]; then
        err "This installer must be run as root."
        echo -e "    Run: ${CYAN}sudo bash <(curl -Ls https://raw.githubusercontent.com/shayanrsh/AutoTrader/main/install.sh)${NC}"
        exit 1
    fi

    # Must be Ubuntu
    if ! grep -qi "ubuntu" /etc/os-release 2>/dev/null; then
        warn "This installer is designed for Ubuntu 24.04 LTS."
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    fi

    # Check disk space (need at least 5GB free)
    FREE_GB=$(df -BG / | tail -1 | awk '{print $4}' | tr -d 'G')
    if [[ ${FREE_GB} -lt 5 ]]; then
        err "Insufficient disk space. Need at least 5GB free, have ${FREE_GB}GB."
        exit 1
    fi

    # Check RAM (need at least 2GB)
    TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
    if [[ ${TOTAL_RAM_MB} -lt 1800 ]]; then
        warn "Low RAM detected (${TOTAL_RAM_MB}MB). Recommended: 4GB+."
    fi

    log "Preflight checks passed (${FREE_GB}GB disk free, ${TOTAL_RAM_MB}MB RAM)"
}

# ── Interactive Mode Selection ──────────────────────────────────────────────
select_mode() {
    echo ""
    echo -e "${BOLD}Select installation mode:${NC}"
    echo ""
    echo -e "  ${CYAN}1)${NC} Full Install    — Everything: system packages, Wine, MT5, Python, firewall, services"
    echo -e "  ${CYAN}2)${NC} App Only        — Bot code + Python venv only (system deps already installed)"
    echo -e "  ${CYAN}3)${NC} Update          — Pull latest code and restart services"
    echo ""
    read -p "Choice [1/2/3]: " -n 1 -r INSTALL_MODE
    echo ""

    case "${INSTALL_MODE}" in
        1) INSTALL_MODE="full" ;;
        2) INSTALL_MODE="app" ;;
        3) INSTALL_MODE="update" ;;
        *) INSTALL_MODE="full" ;;
    esac

    log "Selected mode: ${INSTALL_MODE}"
}

# ============================================================================
# INSTALLATION STEPS
# ============================================================================

install_system_packages() {
    step "1/10" "Installing System Packages"

    export DEBIAN_FRONTEND=noninteractive

    apt update -qq
    apt upgrade -y -qq

    apt install -y -qq \
        software-properties-common \
        wget curl git unzip htop tmux \
        ca-certificates gnupg lsb-release \
        python3 python3-pip python3-venv python3-dev \
        sqlite3 \
        2>/dev/null

    PYTHON_VERSION=$(python3 --version 2>&1)
    log "System packages installed (${PYTHON_VERSION})"
}

install_wine() {
    step "2/10" "Installing Wine"

    dpkg --add-architecture i386
    apt update -qq

    apt install -y -qq wine wine64 wine32 winbind 2>/dev/null

    WINE_VERSION=$(wine --version 2>/dev/null || echo "unknown")
    log "Wine installed (${WINE_VERSION})"
}

install_xvfb() {
    step "3/10" "Installing Xvfb (Virtual Display)"

    apt install -y -qq xvfb x11-utils xauth 2>/dev/null
    log "Xvfb installed"
}

create_trader_user() {
    step "4/10" "Creating '${TRADER_USER}' User"

    if id "${TRADER_USER}" &>/dev/null; then
        log "User '${TRADER_USER}' already exists — skipping"
    else
        adduser --disabled-password --gecos "AutoTrader Service Account" "${TRADER_USER}"
        log "User '${TRADER_USER}' created"
    fi
}

clone_repository() {
    step "5/10" "Downloading AutoTrader"

    if [ -d "${INSTALL_DIR}/.git" ]; then
        info "Repository already exists — pulling latest..."
        sudo -u "${TRADER_USER}" bash -c "cd ${INSTALL_DIR} && git pull --ff-only origin main 2>/dev/null || git pull origin main"
        log "Repository updated"
    else
        mkdir -p "$(dirname ${INSTALL_DIR})"
        rm -rf "${INSTALL_DIR}"
        git clone "${REPO_URL}" "${INSTALL_DIR}"
        chown -R "${TRADER_USER}:${TRADER_USER}" "${INSTALL_DIR}"
        log "Repository cloned to ${INSTALL_DIR}"
    fi

    # Create data directory
    mkdir -p "${INSTALL_DIR}/data"
    chown -R "${TRADER_USER}:${TRADER_USER}" "${INSTALL_DIR}/data"

    # Create config if missing
    if [ ! -f "${INSTALL_DIR}/config.env" ]; then
        sudo -u "${TRADER_USER}" cp "${INSTALL_DIR}/config.env.example" "${INSTALL_DIR}/config.env"
        chmod 600 "${INSTALL_DIR}/config.env"
        warn "Created config.env from template — you MUST edit it before running!"
    fi
}

setup_wine_and_mt5() {
    step "6/10" "Setting Up Wine + MetaTrader 5"

    info "Initializing Wine environment (this takes a minute)..."

    sudo -u "${TRADER_USER}" bash -c "
        export WINEPREFIX=${WINE_PREFIX}
        export WINEARCH=win64
        wineboot --init 2>/dev/null || true
        sleep 5
    " 2>/dev/null

    log "Wine prefix initialized"

    # Download MT5
    MT5_INSTALLER="/tmp/mt5setup.exe"
    if [ ! -f "${MT5_INSTALLER}" ]; then
        info "Downloading MetaTrader 5..."
        wget -q --show-progress -O "${MT5_INSTALLER}" "${MT5_INSTALLER_URL}"
    fi

    info "Installing MetaTrader 5 (headless, ~30 seconds)..."
    sudo -u "${TRADER_USER}" bash -c "
        export WINEPREFIX=${WINE_PREFIX}
        export DISPLAY=:99

        Xvfb :99 -screen 0 1024x768x24 &
        XVFB_PID=\$!
        sleep 2

        wine ${MT5_INSTALLER} /auto &
        sleep 30

        killall mt5setup.exe 2>/dev/null || true
        kill \$XVFB_PID 2>/dev/null || true
    " 2>/dev/null

    log "MetaTrader 5 installed"
}

setup_wine_python() {
    step "7/10" "Installing Python in Wine"

    WIN_PYTHON_INSTALLER="/tmp/python-${WIN_PYTHON_VERSION}-amd64.exe"
    if [ ! -f "${WIN_PYTHON_INSTALLER}" ]; then
        info "Downloading Windows Python ${WIN_PYTHON_VERSION}..."
        wget -q --show-progress -O "${WIN_PYTHON_INSTALLER}" "${WIN_PYTHON_URL}"
    fi

    info "Installing Python in Wine (headless, ~20 seconds)..."
    sudo -u "${TRADER_USER}" bash -c "
        export WINEPREFIX=${WINE_PREFIX}
        export DISPLAY=:99

        Xvfb :99 -screen 0 1024x768x24 &
        XVFB_PID=\$!
        sleep 2

        wine ${WIN_PYTHON_INSTALLER} /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 2>/dev/null
        sleep 20

        kill \$XVFB_PID 2>/dev/null || true
    " 2>/dev/null

    info "Installing MT5 Python packages in Wine..."
    sudo -u "${TRADER_USER}" bash -c "
        export WINEPREFIX=${WINE_PREFIX}
        wine python -m pip install --upgrade pip 2>/dev/null
        wine python -m pip install MetaTrader5 mt5linux 2>/dev/null
    " 2>/dev/null

    log "Wine Python configured with MetaTrader5 + mt5linux"
}

setup_bot_venv() {
    step "8/10" "Setting Up Bot Virtual Environment"

    sudo -u "${TRADER_USER}" bash -c "
        cd ${INSTALL_DIR}
        python3 -m venv venv
        source venv/bin/activate
        pip install --upgrade pip -q
        pip install -r requirements.txt -q 2>/dev/null || pip install -r requirements-dev.txt -q
    "

    log "Python virtual environment ready"
}

setup_firewall() {
    step "9/10" "Configuring Firewall & Security"

    # UFW
    apt install -y -qq ufw fail2ban 2>/dev/null

    ufw default deny incoming 2>/dev/null
    ufw default allow outgoing 2>/dev/null
    ufw allow 22/tcp comment "SSH" 2>/dev/null
    yes | ufw enable 2>/dev/null
    log "UFW firewall enabled (SSH only)"

    # SSH hardening
    SSHD_CONFIG="/etc/ssh/sshd_config"
    cp "${SSHD_CONFIG}" "${SSHD_CONFIG}.bak.$(date +%s)" 2>/dev/null || true

    _ssh_set() {
        local key="$1" val="$2"
        if grep -q "^${key}" "${SSHD_CONFIG}"; then
            sed -i "s/^${key}.*/${key} ${val}/" "${SSHD_CONFIG}"
        else
            echo "${key} ${val}" >> "${SSHD_CONFIG}"
        fi
    }

    _ssh_set "PermitRootLogin" "no"
    _ssh_set "MaxAuthTries" "3"
    _ssh_set "ClientAliveInterval" "300"
    _ssh_set "ClientAliveCountMax" "2"
    _ssh_set "X11Forwarding" "no"
    systemctl restart sshd 2>/dev/null || true
    log "SSH hardened (root login disabled)"

    # fail2ban
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
    systemctl enable fail2ban --now 2>/dev/null
    log "fail2ban active (5 tries → 1h ban)"
}

install_services() {
    step "10/10" "Installing Systemd Services"

    chmod +x "${INSTALL_DIR}/systemd/start_mt5_bridge.sh"

    cp "${INSTALL_DIR}/systemd/mt5-bridge.service" /etc/systemd/system/
    cp "${INSTALL_DIR}/systemd/autotrader.service" /etc/systemd/system/

    systemctl daemon-reload
    systemctl enable mt5-bridge.service
    systemctl enable autotrader.service

    # Create log directory
    mkdir -p /var/log/autotrader
    chown "${TRADER_USER}:${TRADER_USER}" /var/log/autotrader

    log "Systemd services installed and enabled"
}

# ============================================================================
# POST-INSTALL
# ============================================================================

print_success() {
    echo ""
    echo -e "${GREEN}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════════════════╗"
    echo "  ║                                                              ║"
    echo "  ║           ✅  AutoTrader Installed Successfully!  ✅         ║"
    echo "  ║                                                              ║"
    echo "  ╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    echo -e "${BOLD}📋 Next Steps:${NC}"
    echo ""
    echo -e "  ${CYAN}1.${NC} Edit your configuration:"
    echo -e "     ${YELLOW}sudo -u ${TRADER_USER} nano ${INSTALL_DIR}/config.env${NC}"
    echo ""
    echo -e "  ${CYAN}2.${NC} First-time MT5 login (VNC required for GUI):"
    echo -e "     ${YELLOW}apt install -y tigervnc-standalone-server${NC}"
    echo -e "     ${YELLOW}sudo -u ${TRADER_USER} vncserver :1${NC}"
    echo -e "     Connect via VNC → open MT5 → login to Alpari → close VNC"
    echo ""
    echo -e "  ${CYAN}3.${NC} First-time Telegram authentication:"
    echo -e "     ${YELLOW}sudo -u ${TRADER_USER} bash -c 'cd ${INSTALL_DIR} && source venv/bin/activate && python -m src.main'${NC}"
    echo -e "     Enter the verification code when prompted, then Ctrl+C"
    echo ""
    echo -e "  ${CYAN}4.${NC} Start the services:"
    echo -e "     ${YELLOW}sudo systemctl start mt5-bridge${NC}"
    echo -e "     ${YELLOW}sudo systemctl start autotrader${NC}"
    echo ""
    echo -e "  ${CYAN}5.${NC} Monitor logs:"
    echo -e "     ${YELLOW}journalctl -u autotrader -f${NC}"
    echo ""
    echo -e "${BOLD}📖 Full docs:${NC} ${BLUE}https://github.com/shayanrsh/AutoTrader${NC}"
    echo ""
}

print_update_success() {
    echo ""
    echo -e "${GREEN}${BOLD}✅ AutoTrader updated successfully!${NC}"
    echo ""
    echo -e "  Restart services: ${YELLOW}sudo systemctl restart mt5-bridge autotrader${NC}"
    echo ""
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    banner
    preflight
    select_mode

    case "${INSTALL_MODE}" in
        full)
            install_system_packages
            install_wine
            install_xvfb
            create_trader_user
            clone_repository
            setup_wine_and_mt5
            setup_wine_python
            setup_bot_venv
            setup_firewall
            install_services
            print_success
            ;;
        app)
            create_trader_user
            clone_repository
            setup_bot_venv
            install_services
            print_success
            ;;
        update)
            clone_repository
            setup_bot_venv
            install_services
            print_update_success
            ;;
    esac
}

main "$@"
