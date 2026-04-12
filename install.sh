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
RECOMMENDED_DISK_GB=5
MIN_DISK_GB=2
DASHBOARD_CMD="/usr/local/bin/atdash"
DASHBOARD_SCRIPT="${INSTALL_DIR}/scripts/autotrader-dashboard.sh"

USE_TUI=0
INSTALL_MODE=""

# Cache heavy installers between runs to avoid repeated downloads and stale /tmp reliance.
INSTALLER_CACHE_DIR="/var/cache/autotrader"

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

on_error() {
    local line="$1"
    local command="$2"
    local code="$3"
    err "Installer failed (line ${line}, exit=${code})"
    err "Command: ${command}"
    echo -e "${YELLOW}Tip:${NC} Check network access, package mirrors, and service logs."
}

trap 'on_error ${LINENO} "${BASH_COMMAND}" "$?"' ERR

# ── UI Helpers ──────────────────────────────────────────────────────────────
has_tty() {
    [[ -r /dev/tty && -w /dev/tty ]]
}

can_use_tui() {
    has_tty && command -v whiptail >/dev/null 2>&1
}

ui_init() {
    if ! command -v whiptail >/dev/null 2>&1 && has_tty; then
        info "Installing whiptail for interactive TUI..."
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq >/dev/null 2>&1 || true
        apt-get install -y -qq whiptail >/dev/null 2>&1 || true
    fi

    if can_use_tui; then
        USE_TUI=1
        log "Interactive TUI enabled"
    else
        USE_TUI=0
        if has_tty; then
            info "TUI unavailable (whiptail install failed). Using text prompts."
        else
            info "TUI unavailable (no interactive TTY). Using text prompts."
        fi
    fi
}

ui_msg() {
    local title="$1"
    local message="$2"

    if [[ ${USE_TUI} -eq 1 ]]; then
        whiptail --title "${title}" --msgbox "${message}" 14 74 < /dev/tty > /dev/tty 2>&1
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
        whiptail --title "${title}" --yesno "${message}" 14 74 < /dev/tty > /dev/tty 2>&1
        return $?
    fi

    echo ""
    echo -e "${BOLD}${title}${NC}"
    read -r -p "${message} [y/N]: " reply < /dev/tty
    [[ "${reply}" =~ ^[Yy]$ ]]
}

ui_input() {
    local prompt="$1"
    local default_value="$2"

    if [[ ${USE_TUI} -eq 1 ]]; then
        whiptail --title "AutoTrader" --inputbox "${prompt}" 11 74 "${default_value}" 3>&1 1>&2 2>&3 < /dev/tty
        return $?
    fi

    read -r -p "${prompt} [${default_value}]: " value < /dev/tty
    echo "${value:-$default_value}"
}

ui_secret_input() {
    local prompt="$1"
    local default_value="$2"

    if [[ ${USE_TUI} -eq 1 ]]; then
        whiptail --title "AutoTrader" --passwordbox "${prompt}" 11 74 "${default_value}" 3>&1 1>&2 2>&3 < /dev/tty
        return $?
    fi

    read -r -s -p "${prompt}: " value < /dev/tty
    echo ""
    if [[ -z "${value}" ]]; then
        echo "${default_value}"
    else
        echo "${value}"
    fi
}

select_mode() {
    if [[ ${USE_TUI} -eq 1 ]]; then
        local choice
        choice=$(whiptail \
            --title "AutoTrader — Main Menu" \
            --menu "Choose an action:" 20 84 12 \
            "full" "Full install (system deps + Wine + MT5 + app + services)" \
            "app" "App-only install (code + venv + services)" \
            "update" "Update project (git pull + venv deps + restart services)" \
            "setup" "Interactive config wizard (API keys, MT5, risk settings)" \
            "dashboard" "Launch control dashboard (stats + controls)" \
            "quit" "Exit installer" \
            3>&1 1>&2 2>&3 < /dev/tty) || INSTALL_MODE="quit"

        INSTALL_MODE="${choice:-quit}"
    else
        echo ""
        echo -e "${BOLD}Select action:${NC}"
        echo "  1) Full Install"
        echo "  2) App Only"
        echo "  3) Update Project"
        echo "  4) Interactive Config Wizard"
        echo "  5) Launch Dashboard"
        echo "  6) Quit"
        read -r -p "Choice [1-6]: " choice < /dev/tty

        case "${choice}" in
            1) INSTALL_MODE="full" ;;
            2) INSTALL_MODE="app" ;;
            3) INSTALL_MODE="update" ;;
            4) INSTALL_MODE="setup" ;;
            5) INSTALL_MODE="dashboard" ;;
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
    if [[ ${FREE_GB} -lt ${MIN_DISK_GB} ]]; then
        err "Insufficient disk space. Need at least ${MIN_DISK_GB}GB free, have ${FREE_GB}GB."
        exit 1
    fi

    TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
    if [[ ${TOTAL_RAM_MB} -lt 1800 ]]; then
        warn "Low RAM detected (${TOTAL_RAM_MB}MB). Recommended: 4GB+."
    fi

    log "Preflight checks passed (${FREE_GB}GB disk free, ${TOTAL_RAM_MB}MB RAM)"
}

mode_recommended_disk_gb() {
    local mode="$1"
    case "${mode}" in
        full) echo "${RECOMMENDED_DISK_GB}" ;;
        app|update|setup|dashboard) echo "${MIN_DISK_GB}" ;;
        *) echo "${RECOMMENDED_DISK_GB}" ;;
    esac
}

preflight_for_mode() {
    local mode="$1"
    local recommended
    recommended=$(mode_recommended_disk_gb "${mode}")

    if [[ ${FREE_GB} -lt ${recommended} ]]; then
        warn "Low disk space for '${mode}': ${FREE_GB}GB free (recommended: ${recommended}GB)."
        if ! ui_confirm "Low Disk Space" "You have ${FREE_GB}GB free for '${mode}'. Continue anyway?"; then
            return 1
        fi
    fi

    return 0
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
        sudo -u "${TRADER_USER}" bash -lc "set -euo pipefail; cd '${INSTALL_DIR}' && git pull --ff-only origin main 2>/dev/null || git pull origin main"
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

    sudo -u "${TRADER_USER}" bash -lc "
        set -euo pipefail
        export WINEPREFIX=${WINE_PREFIX}
        export WINEARCH=win64
        mkdir -p \"${WINE_PREFIX}\"
        if [[ ! -f \"${WINE_PREFIX}/.autotrader_wine_initialized\" ]]; then
            wineboot --init 2>/dev/null
            touch \"${WINE_PREFIX}/.autotrader_wine_initialized\"
            sleep 5
        fi
    "

    log "Wine prefix initialized"

    mkdir -p "${INSTALLER_CACHE_DIR}"
    MT5_INSTALLER="${INSTALLER_CACHE_DIR}/mt5setup.exe"
    if [ ! -f "${MT5_INSTALLER}" ]; then
        info "Downloading MetaTrader 5..."
        wget -q --show-progress -O "${MT5_INSTALLER}" "${MT5_INSTALLER_URL}"
    fi

    info "Installing MetaTrader 5 (headless, ~30 seconds)..."
    sudo -u "${TRADER_USER}" bash -lc "
        set -euo pipefail
        export WINEPREFIX=${WINE_PREFIX}
        export DISPLAY=:99

        Xvfb :99 -screen 0 1024x768x24 &
        XVFB_PID=\$!
        sleep 2

        wine ${MT5_INSTALLER} /auto &
        sleep 30

        killall mt5setup.exe 2>/dev/null || true
        kill \$XVFB_PID 2>/dev/null || true
    "

    log "MetaTrader 5 installed"
}

setup_wine_python() {
    step "7/10" "Installing Python in Wine"

    mkdir -p "${INSTALLER_CACHE_DIR}"
    WIN_PYTHON_INSTALLER="${INSTALLER_CACHE_DIR}/python-${WIN_PYTHON_VERSION}-amd64.exe"
    if [ ! -f "${WIN_PYTHON_INSTALLER}" ]; then
        info "Downloading Windows Python ${WIN_PYTHON_VERSION}..."
        wget -q --show-progress -O "${WIN_PYTHON_INSTALLER}" "${WIN_PYTHON_URL}"
    fi

    info "Installing Python in Wine (headless, ~20 seconds)..."
    sudo -u "${TRADER_USER}" bash -lc "
        set -euo pipefail
        export WINEPREFIX=${WINE_PREFIX}
        export DISPLAY=:99

        Xvfb :99 -screen 0 1024x768x24 &
        XVFB_PID=\$!
        sleep 2

        wine ${WIN_PYTHON_INSTALLER} /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 2>/dev/null
        sleep 20

        kill \$XVFB_PID 2>/dev/null || true
    "

    info "Installing MT5 Python packages in Wine..."
    sudo -u "${TRADER_USER}" bash -lc "
        set -euo pipefail
        export WINEPREFIX=${WINE_PREFIX}
        wine python -m pip install --upgrade pip 2>/dev/null
        wine python -m pip install MetaTrader5 mt5linux 2>/dev/null
    "

    log "Wine Python configured with MetaTrader5 + mt5linux"
}

setup_bot_venv() {
    step "8/10" "Setting Up Bot Virtual Environment"

    sudo -u "${TRADER_USER}" bash -lc "
        set -euo pipefail
        cd '${INSTALL_DIR}'

        if [[ -d venv ]]; then
            python3 -m venv --upgrade venv
        else
            python3 -m venv venv
        fi

        source venv/bin/activate
        pip install --upgrade pip -q

        if [[ -f requirements.txt ]]; then
            pip install -r requirements.txt -q
        elif [[ -f requirements-dev.txt ]]; then
            pip install -r requirements-dev.txt -q
        else
            echo 'No requirements file found' >&2
            exit 1
        fi
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
    chmod +x "${INSTALL_DIR}/scripts/autotrader-dashboard.sh" 2>/dev/null || true

    cp "${INSTALL_DIR}/systemd/mt5-bridge.service" /etc/systemd/system/
    cp "${INSTALL_DIR}/systemd/autotrader.service" /etc/systemd/system/

    systemctl daemon-reload
    systemctl enable mt5-bridge.service
    systemctl enable autotrader.service

    mkdir -p /var/log/autotrader
    chown "${TRADER_USER}:${TRADER_USER}" /var/log/autotrader

    install_dashboard_command || warn "Dashboard command install skipped"

    log "Systemd services installed and enabled"
}

# ── Utility Actions (TUI extras) ───────────────────────────────────────────
config_file() {
    echo "${INSTALL_DIR}/config.env"
}

require_install_dir() {
    if [[ ! -d "${INSTALL_DIR}" ]]; then
        err "Project not found at ${INSTALL_DIR}. Run install first."
        return 1
    fi
    return 0
}

get_env_value() {
    local key="$1"
    local file
    file="$(config_file)"
    if [[ ! -f "${file}" ]]; then
        echo ""
        return 0
    fi
    grep -E "^${key}=" "${file}" | head -1 | cut -d'=' -f2-
}

set_env_value() {
    local key="$1"
    local value="$2"
    local file
    file="$(config_file)"
    local escaped
    escaped=$(printf '%s' "${value}" | sed 's/[&|]/\\&/g')

    if grep -qE "^${key}=" "${file}"; then
        sed -i "s|^${key}=.*|${key}=${escaped}|" "${file}"
    else
        echo "${key}=${value}" >> "${file}"
    fi
}

prompt_env_value() {
    local key="$1"
    local title="$2"
    local hint="$3"
    local fallback="$4"
    local current
    current=$(get_env_value "${key}")
    if [[ -z "${current}" ]]; then
        current="${fallback}"
    fi

    ui_msg "${title}" "${hint}"
    local entered
    entered=$(ui_input "${key}" "${current}") || return 0
    if [[ -n "${entered}" ]]; then
        set_env_value "${key}" "${entered}"
    fi
}

prompt_env_secret() {
    local key="$1"
    local title="$2"
    local hint="$3"
    local fallback="$4"
    local current
    current=$(get_env_value "${key}")
    if [[ -z "${current}" ]]; then
        current="${fallback}"
    fi

    ui_msg "${title}" "${hint}"
    local entered
    entered=$(ui_secret_input "${key}" "${current}") || return 0
    if [[ -n "${entered}" ]]; then
        set_env_value "${key}" "${entered}"
    fi
}

prepare_config_file() {
    local file
    file="$(config_file)"
    if [[ ! -f "${file}" ]]; then
        if [[ -f "${INSTALL_DIR}/config.env.example" ]]; then
            cp "${INSTALL_DIR}/config.env.example" "${file}"
            chown "${TRADER_USER}:${TRADER_USER}" "${file}" 2>/dev/null || true
            chmod 600 "${file}" 2>/dev/null || true
        else
            err "Missing template: ${INSTALL_DIR}/config.env.example"
            return 1
        fi
    fi
    return 0
}

validate_required_config() {
    local file
    file="$(config_file)"
    local missing=()
    local required_keys=(
        TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_PHONE TELEGRAM_CHANNEL_ID
        NOTIFY_BOT_TOKEN NOTIFY_CHAT_ID GEMINI_API_KEY GROQ_API_KEY
        MT5_ACCOUNT MT5_PASSWORD MT5_SERVER
    )

    for key in "${required_keys[@]}"; do
        local value
        value=$(grep -E "^${key}=" "${file}" | head -1 | cut -d'=' -f2-)
        if [[ -z "${value}" || "${value}" =~ x{3,} || "${value}" == "12345678" || "${value}" == "abcdef1234567890abcdef1234567890" || "${value}" == "+1234567890" || "${value}" == "-1001234567890" || "${value}" == "YourMT5Password" ]]; then
            missing+=("${key}")
        fi
    done

    if (( ${#missing[@]} > 0 )); then
        warn "Configuration still has placeholders/missing values: ${missing[*]}"
        ui_msg "Config Incomplete" "Please complete these keys:\n${missing[*]}\n\nRun the setup wizard again from the menu."
        return 1
    fi

    return 0
}

interactive_config_wizard() {
    require_install_dir || return 1
    prepare_config_file || return 1

    ui_msg "Interactive Setup" "You'll now enter required settings with hints for where to get each value."

    prompt_env_value "TELEGRAM_API_ID" "Telegram API ID" "Get from https://my.telegram.org/apps → API development tools." "12345678"
    prompt_env_value "TELEGRAM_API_HASH" "Telegram API Hash" "Get from the same my.telegram.org app page (32-char hash)." ""
    prompt_env_value "TELEGRAM_PHONE" "Telegram Phone" "Use full international format, e.g. +14155551234." "+1234567890"
    prompt_env_value "TELEGRAM_CHANNEL_ID" "Telegram Channel" "Use channel username (without @) or numeric id (e.g. -100...)." "-1001234567890"

    prompt_env_secret "NOTIFY_BOT_TOKEN" "Notify Bot Token" "Create bot in @BotFather and paste token." ""
    prompt_env_value "NOTIFY_CHAT_ID" "Notify Chat ID" "Message your bot, then use @userinfobot to get your chat ID." "123456789"

    prompt_env_secret "GEMINI_API_KEY" "Gemini API Key" "Get free key at https://aistudio.google.com/apikey" ""
    prompt_env_secret "GROQ_API_KEY" "Groq API Key" "Get free key at https://console.groq.com/keys" ""

    prompt_env_value "MT5_ACCOUNT" "MT5 Account" "From your Alpari account dashboard (MT5 account number)." "12345678"
    prompt_env_secret "MT5_PASSWORD" "MT5 Password" "Your MT5 trading password (not website password)." ""
    prompt_env_value "MT5_SERVER" "MT5 Server" "Typical values: Alpari-MT5 or Alpari-MT5-Demo." "Alpari-MT5"

    if ui_confirm "Dry Run Mode" "Enable DRY_RUN mode for safety on first launch?"; then
        set_env_value "DRY_RUN" "true"
    else
        set_env_value "DRY_RUN" "false"
    fi

    validate_required_config || return 1
    log "Interactive setup completed and saved to $(config_file)"
    return 0
}

backup_config() {
    local file
    file="$(config_file)"
    if [[ ! -f "${file}" ]]; then
        warn "No config file found at ${file}"
        return 0
    fi

    local backup_path="${INSTALL_DIR}/config.env.backup.$(date +%Y%m%d_%H%M%S)"
    cp "${file}" "${backup_path}"
    chown "${TRADER_USER}:${TRADER_USER}" "${backup_path}" 2>/dev/null || true
    chmod 600 "${backup_path}" 2>/dev/null || true

    log "Config backup created: ${backup_path}"
}

open_config_editor() {
    require_install_dir || return 1
    prepare_config_file || return 1

    local editor_choice
    editor_choice=$(ui_input "Editor command" "nano") || return 0
    ${editor_choice} "$(config_file)"
}

install_dashboard_command() {
    if [[ ! -f "${DASHBOARD_SCRIPT}" ]]; then
        warn "Dashboard script missing: ${DASHBOARD_SCRIPT}"
        return 1
    fi

    install -m 755 "${DASHBOARD_SCRIPT}" "${DASHBOARD_CMD}"
    ln -sf "${DASHBOARD_CMD}" /usr/local/bin/atd
    log "Dashboard commands installed: atdash (alias: atd)"
    return 0
}

launch_dashboard() {
    if [[ -x "${DASHBOARD_CMD}" ]]; then
        "${DASHBOARD_CMD}" || true
        return 0
    fi

    warn "Dashboard command not installed yet. Run install first."
    return 1
}

run_update_flow() {
    clone_repository
    setup_bot_venv

    if ui_confirm "Config Wizard" "Run interactive config wizard now?"; then
        interactive_config_wizard || warn "Config wizard was not completed"
    fi

    install_dashboard_command || true

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
    echo -e "  ${CYAN}1.${NC} Open dashboard anytime:"
    echo -e "     ${YELLOW}atdash${NC}  ${BLUE}(short alias: atd)${NC}"
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
            interactive_config_wizard || warn "Config wizard incomplete — edit config.env before going live"
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
            interactive_config_wizard || warn "Config wizard incomplete — edit config.env before going live"
            setup_bot_venv
            install_services
            print_success
            ;;
        update)
            run_update_flow
            ;;
        setup)
            interactive_config_wizard
            ;;
        dashboard)
            launch_dashboard
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
        if [[ "${INSTALL_MODE}" != "quit" ]]; then
            preflight_for_mode "${INSTALL_MODE}" || break
        fi
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
