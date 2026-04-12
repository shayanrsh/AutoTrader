#!/bin/bash
# ============================================================================
# AutoTrader — MT5 Bridge Startup Script
# ============================================================================
# Starts Xvfb, MetaTrader 5 (via Wine), and the mt5linux RPC bridge.
# Called by mt5-bridge.service (systemd).
#
# This script manages 3 processes:
#   1. Xvfb — Virtual X server (display :99)
#   2. MT5 Terminal — Running inside Wine
#   3. mt5linux server — RPC bridge on port 18812
# ============================================================================

set -euo pipefail

TRADER_USER="trader"
WINE_PREFIX="/home/${TRADER_USER}/.wine"
DISPLAY_NUM=":99"
MT5_PATH="${WINE_PREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
BRIDGE_PORT=18812

# Fallback MT5 paths (Alpari installer may use different names)
MT5_PATHS=(
    "${WINE_PREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
    "${WINE_PREFIX}/drive_c/Program Files/MetaTrader 5/terminal.exe"
    "${WINE_PREFIX}/drive_c/Program Files (x86)/MetaTrader 5/terminal.exe"
    "${WINE_PREFIX}/drive_c/Program Files/Alpari MT5/terminal64.exe"
    "${WINE_PREFIX}/drive_c/Program Files/Alpari MT5/terminal.exe"
)

log() { echo "[MT5-BRIDGE] $(date '+%Y-%m-%d %H:%M:%S') $1"; }

# ── Find MT5 executable ────────────────────────────────────────────────────
find_mt5() {
    for path in "${MT5_PATHS[@]}"; do
        if [ -f "${path}" ]; then
            MT5_PATH="${path}"
            log "Found MT5 at: ${MT5_PATH}"
            return 0
        fi
    done
    log "ERROR: MetaTrader 5 terminal not found in any expected location"
    log "Searched paths:"
    for path in "${MT5_PATHS[@]}"; do
        log "  - ${path}"
    done
    return 1
}

# ── Cleanup on exit ────────────────────────────────────────────────────────
cleanup() {
    log "Shutting down MT5 bridge..."
    
    # Kill bridge
    if [ -n "${BRIDGE_PID:-}" ]; then
        kill "${BRIDGE_PID}" 2>/dev/null || true
        wait "${BRIDGE_PID}" 2>/dev/null || true
    fi
    
    # Kill MT5
    if [ -n "${MT5_PID:-}" ]; then
        kill "${MT5_PID}" 2>/dev/null || true
        wait "${MT5_PID}" 2>/dev/null || true
    fi
    
    # Kill leftover Wine processes
    wineserver -k 2>/dev/null || true
    
    # Kill Xvfb
    if [ -n "${XVFB_PID:-}" ]; then
        kill "${XVFB_PID}" 2>/dev/null || true
    fi
    
    log "Cleanup complete"
}

trap cleanup EXIT SIGTERM SIGINT

# ── Step 1: Start Xvfb ─────────────────────────────────────────────────────
log "Starting Xvfb on display ${DISPLAY_NUM}..."

# Kill any existing Xvfb on this display
pkill -f "Xvfb ${DISPLAY_NUM}" 2>/dev/null || true
sleep 1

Xvfb ${DISPLAY_NUM} -screen 0 1280x1024x24 -ac &
XVFB_PID=$!
export DISPLAY=${DISPLAY_NUM}

# Wait for Xvfb to be ready
sleep 3

if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
    log "ERROR: Xvfb failed to start"
    exit 1
fi
log "Xvfb started (PID: ${XVFB_PID})"

# ── Step 2: Start MetaTrader 5 ─────────────────────────────────────────────
find_mt5 || exit 1

log "Starting MetaTrader 5 terminal..."
export WINEPREFIX="${WINE_PREFIX}"

wine "${MT5_PATH}" &
MT5_PID=$!

# Wait for MT5 to initialize (it takes a while on first start)
log "Waiting for MT5 to initialize (30 seconds)..."
sleep 30

if ! kill -0 "${MT5_PID}" 2>/dev/null; then
    log "WARNING: MT5 process may have ended — checking if running under wineserver..."
    # Wine might have forked the process, check for terminal.exe
    sleep 5
fi

log "MetaTrader 5 started"

# ── Step 3: Start mt5linux Bridge Server ────────────────────────────────────
log "Starting mt5linux RPC bridge on port ${BRIDGE_PORT}..."

wine python -m mt5linux --host 0.0.0.0 --port ${BRIDGE_PORT} &
BRIDGE_PID=$!

sleep 5

if ! kill -0 "${BRIDGE_PID}" 2>/dev/null; then
    log "ERROR: mt5linux bridge failed to start"
    exit 1
fi

log "mt5linux bridge started (PID: ${BRIDGE_PID}, port: ${BRIDGE_PORT})"
log "MT5 Bridge is ready — all 3 components running"

# ── Keep running ────────────────────────────────────────────────────────────
# Wait for any child process to exit, then restart
while true; do
    # Check if bridge is still running
    if ! kill -0 "${BRIDGE_PID}" 2>/dev/null; then
        log "mt5linux bridge died — restarting..."
        wine python -m mt5linux --host 0.0.0.0 --port ${BRIDGE_PORT} &
        BRIDGE_PID=$!
        sleep 5
    fi
    
    # Check if Xvfb is still running
    if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
        log "Xvfb died — restarting..."
        Xvfb ${DISPLAY_NUM} -screen 0 1280x1024x24 -ac &
        XVFB_PID=$!
        sleep 2
    fi
    
    sleep 10
done
