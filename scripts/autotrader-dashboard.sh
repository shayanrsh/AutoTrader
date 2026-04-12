#!/bin/bash
set -euo pipefail

INSTALL_DIR="${AUTOTRADER_DIR:-/home/trader/autotrader}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

fail() {
    echo -e "${RED}[x]${NC} $1"
    exit 1
}

info() {
    echo -e "${GREEN}[i]${NC} $1"
}

ensure_install() {
    [[ -d "${INSTALL_DIR}" ]] || fail "AutoTrader not found at ${INSTALL_DIR}"
}

run_text_fallback() {
    echo -e "${YELLOW}[!]${NC} Modern Textual UI is unavailable in the current environment."
    echo ""
    echo "Quick commands:"
    echo "  systemctl status autotrader mt5-bridge"
    echo "  sudo systemctl restart autotrader mt5-bridge"
    echo "  journalctl -u autotrader -n 80 --no-pager"
    echo "  journalctl -u mt5-bridge -n 80 --no-pager"
    echo ""
    echo "Install Textual runtime and retry:"
    echo "  cd ${INSTALL_DIR}"
    echo "  source venv/bin/activate"
    echo "  pip install -r requirements.txt"
}

launch_modern_tui() {
    local candidate

    if [[ -x "${INSTALL_DIR}/venv/bin/python" ]]; then
        candidate="${INSTALL_DIR}/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        candidate="$(command -v python3)"
    else
        run_text_fallback
        return 0
    fi

    if "${candidate}" -c "import textual" >/dev/null 2>&1; then
        exec "${candidate}" -m src.tui
    fi

    run_text_fallback
}

main() {
    ensure_install
    cd "${INSTALL_DIR}"
    launch_modern_tui
}

main "$@"
