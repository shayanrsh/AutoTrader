#!/bin/bash
# Debug script to test MT5 terminal startup in Wine

set -eo pipefail

TRADER_USER="trader"
WINE_PREFIX="/home/${TRADER_USER}/.wine"
MT5_PATH="${WINE_PREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
DISPLAY=":100"
WINEARCH="win64"

echo "========================================="
echo "MT5 Terminal Startup Test"
echo "========================================="
echo "Configuration:"
echo "  Trader user: $TRADER_USER"
echo "  Wine prefix: $WINE_PREFIX"
echo "  Display: $DISPLAY"
echo "  MT5 path: $MT5_PATH"
echo "========================================="

# Ensure MT5 binary exists
if [ ! -f "$MT5_PATH" ]; then
    echo "ERROR: MT5 terminal not found at: $MT5_PATH"
    exit 1
fi

echo "✓ MT5 terminal found"

# Check if display is available
echo ""
echo "Testing display availability..."
if ! DISPLAY="$DISPLAY" xset q >/dev/null 2>&1; then
    echo "  - Display $DISPLAY not responding, trying to start Xvfb..."
    # Try to start Xvfb on :100
    Xvfb "$DISPLAY" -screen 0 1280x1024x24 -ac &
    XVFB_PID=$!
    sleep 2
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "  ERROR: Failed to start Xvfb"
        exit 1
    fi
    echo "  ✓ Xvfb started (PID: $XVFB_PID)"
fi

echo "✓ Display available"

# Test MT5 startup
echo ""
echo "Attempting to start MT5 terminal as $TRADER_USER..."
echo "Command: WINEPREFIX=$WINE_PREFIX WINEARCH=$WINEARCH DISPLAY=$DISPLAY wine \"$MT5_PATH\""
echo ""

export WINEPREFIX="$WINE_PREFIX"
export WINEARCH="$WINEARCH"
export DISPLAY="$DISPLAY"

timeout 30 sudo -u "$TRADER_USER" bash -lc "wine \"$MT5_PATH\"" 2>&1 || {
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 124 ]; then
        echo ""
        echo "✓ MT5 terminal started and ran for 30 seconds (timeout)"
        echo "  This suggests MT5 is launching correctly."
    else
        echo ""
        echo "✗ MT5 terminal exited with code: $EXIT_CODE"
    fi
}

echo ""
echo "Testing if any Wine processes are running..."
ps aux | grep -E "wine|terminal" | grep -v grep || echo "  (none)"

# Cleanup
if [ -n "${XVFB_PID:-}" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
    kill "$XVFB_PID" 2>/dev/null || true
    echo ""
    echo "✓ Xvfb stopped"
fi

echo ""
echo "Test complete."
