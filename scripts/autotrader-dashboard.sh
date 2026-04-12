#!/bin/bash
set -euo pipefail

INSTALL_DIR="${AUTOTRADER_DIR:-/home/trader/autotrader}"
CONFIG_FILE="${INSTALL_DIR}/config.env"
HEALTH_URL="http://localhost:8080/health"
METRICS_URL="http://localhost:8080/metrics"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

has_tty() {
    [[ -r /dev/tty && -w /dev/tty ]]
}

has_tui() {
    has_tty && command -v whiptail >/dev/null 2>&1
}

info() { echo -e "${BLUE}[i]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
ok() { echo -e "${GREEN}[✓]${NC} $1"; }

ensure_requirements() {
    if [[ ! -d "${INSTALL_DIR}" ]]; then
        echo -e "${RED}AutoTrader not found at ${INSTALL_DIR}${NC}"
        exit 1
    fi
}

service_state() {
    local name="$1"
    systemctl is-active "${name}" 2>/dev/null || echo "unknown"
}

json_value() {
    local json="$1"
    local key="$2"
    python3 - "$key" <<'PY' <<<"$json"
import json
import sys
key = sys.argv[1]
try:
    data = json.loads(sys.stdin.read())
    val = data.get(key, "n/a")
    print(val)
except Exception:
    print("n/a")
PY
}

render_overview_text() {
    local disk ram mt5_status app_status health_raw metrics_raw
    disk=$(df -h / | awk 'END{print $4 " free / " $2 " total"}')
    ram=$(free -m | awk '/^Mem:/{printf "%sMB used / %sMB total", $3, $2}')
    mt5_status=$(service_state mt5-bridge)
    app_status=$(service_state autotrader)

    health_raw=$(curl -sS --max-time 2 "${HEALTH_URL}" 2>/dev/null || echo "{}")
    metrics_raw=$(curl -sS --max-time 2 "${METRICS_URL}" 2>/dev/null || echo "{}")

    local mode open_trades daily_pnl signals_processed trades_executed errors_count
    mode=$(json_value "${health_raw}" "dry_run")
    open_trades=$(json_value "${health_raw}" "open_trades")
    daily_pnl=$(json_value "${health_raw}" "daily_pnl")
    signals_processed=$(json_value "${metrics_raw}" "signals_processed")
    trades_executed=$(json_value "${metrics_raw}" "trades_executed")
    errors_count=$(json_value "${metrics_raw}" "errors_count")

    cat <<EOF
AutoTrader Dashboard

Install Dir: ${INSTALL_DIR}
Disk: ${disk}
RAM: ${ram}

Service Status:
- mt5-bridge: ${mt5_status}
- autotrader: ${app_status}

Runtime Stats:
- dry_run: ${mode}
- open_trades: ${open_trades}
- daily_pnl: ${daily_pnl}
- signals_processed: ${signals_processed}
- trades_executed: ${trades_executed}
- errors_count: ${errors_count}

Health endpoint: ${HEALTH_URL}
Quick return command: atdash
EOF
}

show_overview() {
    local text
    text="$(render_overview_text)"

    if has_tui; then
        whiptail --title "AutoTrader Overview" --msgbox "$text" 24 90 < /dev/tty > /dev/tty 2>&1
    else
        echo "$text"
    fi
}

service_action() {
    local action="$1"
    case "$action" in
        start)
            systemctl start mt5-bridge autotrader
            ok "Services started"
            ;;
        stop)
            systemctl stop autotrader mt5-bridge
            ok "Services stopped"
            ;;
        restart)
            systemctl restart mt5-bridge autotrader
            ok "Services restarted"
            ;;
        *)
            warn "Unknown action: $action"
            ;;
    esac
}

toggle_dry_run() {
    if [[ ! -f "${CONFIG_FILE}" ]]; then
        warn "Config file not found at ${CONFIG_FILE}"
        return 1
    fi

    local current
    current=$(grep -E '^DRY_RUN=' "${CONFIG_FILE}" | cut -d'=' -f2- || true)

    if [[ "${current}" == "true" ]]; then
        sed -i 's/^DRY_RUN=.*/DRY_RUN=false/' "${CONFIG_FILE}"
        ok "DRY_RUN set to false"
    else
        if grep -q '^DRY_RUN=' "${CONFIG_FILE}"; then
            sed -i 's/^DRY_RUN=.*/DRY_RUN=true/' "${CONFIG_FILE}"
        else
            echo 'DRY_RUN=true' >> "${CONFIG_FILE}"
        fi
        ok "DRY_RUN set to true"
    fi

    systemctl restart autotrader 2>/dev/null || true
}

run_update() {
    if [[ ! -d "${INSTALL_DIR}/.git" ]]; then
        warn "Git repository not found at ${INSTALL_DIR}"
        return 1
    fi

    sudo -u trader bash -c "cd ${INSTALL_DIR} && git pull --ff-only origin main"
    sudo -u trader bash -c "cd ${INSTALL_DIR} && source venv/bin/activate && pip install -r requirements.txt -q"
    systemctl restart mt5-bridge autotrader
    ok "Project updated and services restarted"
}

show_logs() {
    local unit="$1"
    journalctl -u "$unit" -n 80 --no-pager || true
}

main_menu_tui() {
    while true; do
        local choice
        choice=$(whiptail \
            --title "AutoTrader Dashboard" \
            --menu "Select an action" 22 86 14 \
            "overview" "View full status & runtime stats" \
            "start" "Start services" \
            "stop" "Stop services" \
            "restart" "Restart services" \
            "toggle-dry" "Toggle DRY_RUN and restart bot" \
            "update" "Update project + deps + restart" \
            "logs-bot" "View autotrader logs" \
            "logs-bridge" "View mt5-bridge logs" \
            "exit" "Exit dashboard" \
            3>&1 1>&2 2>&3 < /dev/tty) || choice="exit"

        case "$choice" in
            overview) show_overview ;;
            start) service_action start ;;
            stop) service_action stop ;;
            restart) service_action restart ;;
            toggle-dry) toggle_dry_run ;;
            update) run_update ;;
            logs-bot) show_logs autotrader ;;
            logs-bridge) show_logs mt5-bridge ;;
            *) break ;;
        esac
    done
}

main_menu_text() {
    while true; do
        echo ""
        echo -e "${BOLD}AutoTrader Dashboard${NC}"
        echo "1) Overview"
        echo "2) Start services"
        echo "3) Stop services"
        echo "4) Restart services"
        echo "5) Toggle DRY_RUN"
        echo "6) Update project"
        echo "7) Bot logs"
        echo "8) Bridge logs"
        echo "9) Exit"
        read -r -p "Choice [1-9]: " choice < /dev/tty

        case "$choice" in
            1) show_overview ;;
            2) service_action start ;;
            3) service_action stop ;;
            4) service_action restart ;;
            5) toggle_dry_run ;;
            6) run_update ;;
            7) show_logs autotrader ;;
            8) show_logs mt5-bridge ;;
            *) break ;;
        esac
    done
}

main() {
    ensure_requirements

    if has_tui; then
        main_menu_tui
    else
        info "whiptail unavailable; using text dashboard"
        main_menu_text
    fi
}

main "$@"
