#!/bin/bash
# Diagnostic script to test mt5linux bridge startup with detailed logging

set -eo pipefail

TRADER_USER="trader"
WINE_PREFIX="/home/${TRADER_USER}/.wine"
DISPLAY=":99"
BRIDGE_PORT=18812
LOG_FILE="/tmp/mt5-bridge-diagnostic-$(date +%s).log"

{
    echo "========================================="
    echo "MT5 Bridge Diagnostic Test"
    echo "========================================="
    echo "Started: $(date)"
    echo "Log file: $LOG_FILE"
    echo ""
    
    echo "[1] Checking system prerequisites..."
    echo "  - Xvfb: $(which Xvfb || echo 'NOT FOUND')"
    echo "  - Wine: $(which wine || echo 'NOT FOUND')"
    echo "  - Python: $(which python || echo 'NOT FOUND')"
    echo "  - trader user: $(id trader 2>&1 | head -1)"
    echo ""
    
    echo "[2] Checking MT5 installation..."
    MT5_PATH="/home/${TRADER_USER}/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"
    if [ -f "$MT5_PATH" ]; then
        echo "  ✓ MT5 terminal found"
        ls -lh "$MT5_PATH"
    else
        echo "  ✗ MT5 terminal NOT FOUND at: $MT5_PATH"
        exit 1
    fi
    echo ""
    
    echo "[3] Checking if Bridge is already running..."
    if ss -tln 2>/dev/null | grep -q ":${BRIDGE_PORT}[[:space:]]"; then
        echo "  ✓ Port ${BRIDGE_PORT} already listening"
        echo "    $(ss -tln | grep ":${BRIDGE_PORT}")"
    else
        echo "  - Port ${BRIDGE_PORT} not listening yet"
    fi
    echo ""
    
    echo "[4] Starting bridge with our new script..."
    bash /root/Projects/AutoTrader/systemd/start_mt5_bridge.sh &
    BRIDGE_SCRIPT_PID=$!
    echo "  Bridge script started with PID: $BRIDGE_SCRIPT_PID"
    echo ""
    
    echo "[5] Waiting for bridge to initialize (60 seconds)..."
    TIMEOUT=60
    ELAPSED=0
    while [ $ELAPSED -lt $TIMEOUT ]; do
        if ss -tln 2>/dev/null | grep -q ":${BRIDGE_PORT}[[:space:]]"; then
            echo "  ✓ Bridge port ${BRIDGE_PORT} is now listening!"
            echo "    $(ss -tln | grep ":${BRIDGE_PORT}")"
            
            echo ""
            echo "[6] Checking bridge processes..."
            ps aux | grep -E "wine|python.*mt5linux|Xvfb" | grep -v grep
            
            echo ""
            echo "[7] Testing MT5 connection..."
            timeout 30 python3 /root/Projects/AutoTrader/debug_mt5_connection.py 2>&1 || {
                echo "  Connection test returned code: $?"
            }
            
            echo ""
            echo "========================================="
            echo "Test completed: $(date)"
            echo "========================================="
            exit 0
        fi
        
        sleep 1
        ELAPSED=$((ELAPSED + 1))
        if [ $((ELAPSED % 10)) -eq 0 ]; then
            echo "  ... waiting ($ELAPSED/$TIMEOUT seconds)"
        fi
    done
    
    echo "  ✗ Bridge did not start within ${TIMEOUT} seconds"
    echo ""
    echo "[ERROR] Bridge startup failed. Check logs:"
    tail -30 /home/trader/autotrader/data/mt5-bridge-manual.log 2>/dev/null || echo "  (log not found)"
    
    exit 1
    
} 2>&1 | tee "$LOG_FILE"
