#!/bin/bash
set -euo pipefail

INSTALL_DIR="${AUTOTRADER_DIR:-/home/trader/autotrader}"
INSTALL_SCRIPT="${INSTALL_DIR}/install.sh"

if [[ ! -f "${INSTALL_SCRIPT}" ]]; then
    echo "AutoTrader installer not found at ${INSTALL_SCRIPT}"
    exit 1
fi

if [[ $EUID -ne 0 ]]; then
    exec sudo bash "${INSTALL_SCRIPT}" --textual-installer
fi

exec bash "${INSTALL_SCRIPT}" --textual-installer
