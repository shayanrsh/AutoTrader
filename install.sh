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
#    curl -fsSL https://raw.githubusercontent.com/shayanrsh/AutoTrader/main/install.sh | sudo bash
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

USE_TUI=0
INSTALL_MODE=""

# ── Banner ──────────────────────────────────────────────────────────────────
banner() {
    echo ""
    echo -e "${CYAN}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════════════════╗"
    echo "  ║                                                              ║"
    echo "  ║                🤖  AutoTrader Installer  🤖                 ║"
    echo "  ║                                                              ║"
    echo "  ║   Telegram Signals → AI Parsing → MT5 Execution            ║"
    echo "  ║   Full setup, updates, service tools, and health checks    ║"
    echo "  ║                                                              ║"
    echo "  ╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
info() { echo -e "${BLUE}[i]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
step() { echo -e "\n${MAGENTA}${BOLD}━━━ Step $1: $2 ━━━${NC}"; }

# ── UI Helpers ──────────────────────────────────────────────────────────────
can_use_tui() {
    [[ -t 0 && -t 1 ]] && command -v whiptail >/dev/null 2>&1
}

ui_init() {
    if can_use_tui; then
        USE_TUI=1
    else
        USE_TUI=0
        info "TUI unavailable (missing terminal or whiptail). Using text prompts."
    fi
}

ui_msg() {
    local title="$1"
    local message="$2"

    if [[ ${USE_TUI} -eq 1 ]]; then
        whiptail --title "${title}" --msgbox "${message}" 14 74
    else
        echo ""
        echo -e "${BOLD}${title}${NC}"
        echo -e "${message}"
    fi
}

ui_confirm() {
    local title="$1"
    local message="$2"

    if [[ ${USE_TUI} -eq 1 ]]; then
        whiptail --title "${title}" --yesno "${message}" 14 74
        return $?
    fi

    echo ""
    echo -e "${BOLD}${title}${NC}"
    read -r -p "${message} [y/N]: " reply
    [[ "${reply}" =~ ^[Yy]$ ]]
}

ui_input() {
    local prompt="$1"
    local default_value="$2"

    if [[ ${USE_TUI} -eq 1 ]]; then
        whiptail --title "AutoTrader" --inputbox "${prompt}" 11 74 "${default_value}" 3>&1 1>&2 2>&3
        return $?
    fi

    read -r -p "${prompt} [${default_value}]: " value
    echo "${value:-$default_value}"
}

select_mode() {
    if [[ ${USE_TUI} -eq 1 ]]; then
        local choice
        choice=$(whiptail \
            --title "AutoTrader — Main Menu" \
            --menu "Choose an action:" 21 84 12 \
            "full" "Full install (system deps + Wine + MT5 + app + services)" \
            "app" "App-only install (code + venv + services)" \
            "update" "Update project (git pull + venv deps + restart services)" \
            "status" "System status dashboard" \
            "services" "Service manager (start/stop/restart/status/logs)" \
            "health" "Health check (localhost:8080/health)" \
            "backup" "Backup config.env" \
            "config" "Open config.env editor" \
            "quit" "Exit installer" \
            3>&1 1>&2 2>&3) || INSTALL_MODE="quit"

        INSTALL_MODE="${choice:-quit}"
    else
        echo ""
        echo -e "${BOLD}Select action:${NC}"
        echo "  1) Full Install"
        echo "  2) App Only"
        echo "  3) Update Project"
        echo "  4) System Status"
        echo "  5) Service Manager"
        echo "  6) Health Check"
        echo "  7) Backup config.env"
        echo "  8) Edit config.env"
        echo "  9) Quit"
        read -r -p "Choice [1-9]: " choice

        case "${choice}" in
            1) INSTALL_MODE="full" ;;
            2) INSTALL_MODE="app" ;;
            3) INSTALL_MODE="update" ;;
            4) INSTALL_MODE="status" ;;
            5) INSTALL_MODE="services" ;;
            6) INSTALL_MODE="health" ;;
            7) INSTALL_MODE="backup" ;;
            8) INSTALL_MODE="config" ;;
            *) INSTALL_MODE="quit" ;;
        esac
    fi

    log "Selected action: ${INSTALL_MODE}"
}

# ── Preflight Checks ───────────────────────────────────────────────────────
preflight() {
    if [[ $EUID -ne 0 ]]; then
        err "This installer must be run as root."
        echo -e "    Run: ${CYAN}curl -fsSL https://raw.githubusercontent.com/shayanrsh/AutoTrader/main/install.sh | sudo bash${NC}"
        exit 1
    fi

    if ! grep -qi "ubuntu" /etc/os-release 2>/dev/null; then
        warn "This installer is designed for Ubuntu 24.04 LTS."
        if ! ui_confirm "Compatibility Warning" "This installer is optimized for Ubuntu 24.04 LTS. Continue anyway?"; then
            exit 1
        fi
    fi

    FREE_GB=$(df -BG / | tail -1 | awk '{print $4}' | tr -d 'G')
    if [[ ${FREE_GB} -lt 5 ]]; then
        err "Insufficient disk space. Need at least 5GB free, have ${FREE_GB}GB."
        exit 1
    fi

    TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
    if [[ ${TOTAL_RAM_MB} -lt 1800 ]]; then
        warn "Low RAM detected (${TOTAL_RAM_MB}MB). Recommended: 4GB+."
    fi

    log "Preflight checks passed (${FREE_GB}GB disk free, ${TOTAL_RAM_MB}MB RAM)"
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

    mkdir -p "${INSTALL_DIR}/data"
    chown -R "${TRADER_USER}:${TRADER_USER}" "${INSTALL_DIR}/data"

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

    apt install -y -qq ufw fail2ban 2>/dev/null

    ufw default deny incoming 2>/dev/null
    ufw default allow outgoing 2>/dev/null
    ufw allow 22/tcp comment "SSH" 2>/dev/null
    yes | ufw enable 2>/dev/null
    log "UFW firewall enabled (SSH only)"

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

    mkdir -p /var/log/autotrader
    chown "${TRADER_USER}:${TRADER_USER}" /var/log/autotrader

    log "Systemd services installed and enabled"
}

# ── Utility Actions (TUI extras) ───────────────────────────────────────────
backup_config() {
    if [[ ! -f "${INSTALL_DIR}/config.env" ]]; then
        warn "No config file found at ${INSTALL_DIR}/config.env"
        return 0
    fi

    local backup_path="${INSTALL_DIR}/config.env.backup.$(date +%Y%m%d_%H%M%S)"
    cp "${INSTALL_DIR}/config.env" "${backup_path}"
    chown "${TRADER_USER}:${TRADER_USER}" "${backup_path}" 2>/dev/null || true
    chmod 600 "${backup_path}" 2>/dev/null || true

    log "Config backup created: ${backup_path}"
    ui_msg "Backup Complete" "Config backup created:\n${backup_path}"
}

open_config_editor() {
    if [[ ! -f "${INSTALL_DIR}/config.env" ]]; then
        warn "No config file found; creating from template."
        if [[ -f "${INSTALL_DIR}/config.env.example" ]]; then
            cp "${INSTALL_DIR}/config.env.example" "${INSTALL_DIR}/config.env"
            chown "${TRADER_USER}:${TRADER_USER}" "${INSTALL_DIR}/config.env" 2>/dev/null || true
            chmod 600 "${INSTALL_DIR}/config.env" 2>/dev/null || true
        else
            err "Template not found at ${INSTALL_DIR}/config.env.example"
            return 1
        fi
    fi

    local editor_choice
    editor_choice=$(ui_input "Editor command" "nano") || return 0
    ${editor_choice} "${INSTALL_DIR}/config.env"
}

show_health_check() {
    local output
    if output=$(curl -sS --max-time 3 http://localhost:8080/health 2>/dev/null); then
        ui_msg "Health Check" "${output}"
    else
        warn "Health endpoint unreachable on localhost:8080"
        ui_msg "Health Check" "Health endpoint unreachable on localhost:8080"
    fi
}

show_status_dashboard() {
    local disk ram mt5_status app_status text
    disk=$(df -h / | tail -1 | awk '{print $4 " free of " $2}')
    ram=$(free -m | awk '/^Mem:/{printf "%sMB used / %sMB total", $3, $2}')
    mt5_status=$(systemctl is-active mt5-bridge.service 2>/dev/null || echo "unknown")
    app_status=$(systemctl is-active autotrader.service 2>/dev/null || echo "unknown")

    text="Install path: ${INSTALL_DIR}\nDisk: ${disk}\nRAM: ${ram}\nmt5-bridge.service: ${mt5_status}\nautotrader.service: ${app_status}"

    if [[ ${USE_TUI} -eq 1 ]]; then
        whiptail --title "AutoTrader Status" --msgbox "${text}" 14 78
    else
        echo ""
        echo -e "${BOLD}AutoTrader Status${NC}"
        echo -e "${text}"
    fi
}

service_manager() {
    local action

    if [[ ${USE_TUI} -eq 1 ]]; then
        action=$(whiptail \
            --title "Service Manager" \
            --menu "Choose service action:" 18 76 10 \
            "start" "Start mt5-bridge + autotrader" \
            "stop" "Stop mt5-bridge + autotrader" \
            "restart" "Restart mt5-bridge + autotrader" \
            "status" "Show current status" \
            "logs" "Show last 40 log lines" \
            "back" "Back to main menu" \
            3>&1 1>&2 2>&3) || action="back"
    else
        echo ""
        echo "Service actions: start | stop | restart | status | logs | back"
        read -r -p "Action: " action
    fi

    case "${action}" in
        start)
            systemctl start mt5-bridge autotrader
            log "Services started"
            ;;
        stop)
            systemctl stop autotrader mt5-bridge
            log "Services stopped"
            ;;
        restart)
            systemctl restart mt5-bridge autotrader
            log "Services restarted"
            ;;
        status)
            systemctl --no-pager status mt5-bridge autotrader || true
            ;;
        logs)
            journalctl -u mt5-bridge -u autotrader -n 40 --no-pager || true
            ;;
        *)
            return 0
            ;;
    esac
}

run_update_flow() {
    clone_repository
    setup_bot_venv

    if ui_confirm "Restart Services" "Update finished. Restart mt5-bridge and autotrader now?"; then
        systemctl restart mt5-bridge autotrader 2>/dev/null || true
        log "Services restarted"
    else
        warn "Services not restarted automatically."
    fi

    print_update_success
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

run_mode() {
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
            run_update_flow
            ;;
        status)
            show_status_dashboard
            ;;
        services)
            service_manager
            ;;
        health)
            show_health_check
            ;;
        backup)
            backup_config
            ;;
        config)
            open_config_editor
            ;;
        quit)
            info "Exiting installer."
            return 1
            ;;
        *)
            warn "Unknown action: ${INSTALL_MODE}"
            return 1
            ;;
    esac

    return 0
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    banner
    ui_init
    preflight

    while true; do
        select_mode
        if ! run_mode; then
            break
        fi

        if ! ui_confirm "Continue" "Return to main menu?"; then
            break
        fi
    done

    log "Installer finished."
}

main "$@"
