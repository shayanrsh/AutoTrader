#!/bin/bash
# ============================================================================
# AutoTrader — MT5 Bridge Startup Script (FIXED)
# ============================================================================
# Starts Xvfb, MetaTrader 5 (via Wine), and the mt5linux RPC bridge.
# Called by mt5-bridge.service (systemd).
#
# CRITICAL FIX: Runs all Wine/mt5linux operations as 'trader' user!
# Wine requires the prefix to be owned by the running user.
#
# This script manages 3 processes:
#   1. Xvfb — Virtual X server (display :99) — runs as root
#   2. MT5 Terminal — Running inside Wine — runs as trader  
#   3. mt5linux server — RPC bridge on port 18812 — runs as trader
#
# ============================================================================

set -euo pipefail

TRADER_USER="trader"
WINE_PREFIX="/home/${TRADER_USER}/.wine"
DISPLAY_NUM="${DISPLAY_NUM:-:99}"
MT5_PATH="${WINE_PREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
BRIDGE_PORT=18812
BRIDGE_TTY_LOG="/tmp/mt5linux-bridge-${BRIDGE_PORT}.typescript"
XVFB_LOCK_FILE="/tmp/.X${DISPLAY_NUM#:}-lock"
MT5_STARTUP_TIMEOUT=60  # Seconds to wait for MT5 to initialize
BRIDGE_STARTUP_TIMEOUT=20  # Seconds to wait for bridge to start
INIT_SLEEP=2  # Initial delay before checking processes

# Fallback MT5 paths (Alpari installer may use different names)
MT5_PATHS=(
    "${WINE_PREFIX}/drive_c/Program Files/MetaTrader 5/terminal64.exe"
    "${WINE_PREFIX}/drive_c/Program Files/MetaTrader 5/terminal.exe"
    "${WINE_PREFIX}/drive_c/Program Files (x86)/MetaTrader 5/terminal.exe"
    "${WINE_PREFIX}/drive_c/Program Files/Alpari MT5/terminal64.exe"
    "${WINE_PREFIX}/drive_c/Program Files/Alpari MT5/terminal.exe"
)

log() { 
    local level="$1"
    shift
    echo "[MT5-BRIDGE] $(date '+%Y-%m-%d %H:%M:%S') [$level] $*" 
    # Also log to syslog if available
    command -v logger >/dev/null 2>&1 && logger -t mt5-bridge -p "user.${level,,}" "$*" || true
}

error_exit() {
    log "ERROR" "$@"
    exit 1
}

is_process_running() {
    local pid="$1"
    kill -0 "$pid" 2>/dev/null
}

wait_for_process() {
    local pid="$1"
    local name="$2"
    local timeout="$3"
    local elapsed=0
    
    log "INFO" "Waiting for $name (PID: $pid) to initialize (${timeout}s timeout)..."
    
    while [ $elapsed -lt $timeout ]; do
        if ! is_process_running "$pid"; then
            log "ERROR" "$name process (PID: $pid) died prematurely"
            return 1
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    
    if is_process_running "$pid"; then
        log "INFO" "$name initialized successfully"
        return 0
    else
        log "ERROR" "$name did not initialize within ${timeout}s"
        return 1
    fi
}

wait_for_port() {
    local port="$1"
    local timeout="$2"
    local elapsed=0
    
    log "INFO" "Waiting for port $port to be available (${timeout}s timeout)..."
    
    while [ $elapsed -lt $timeout ]; do
        if ss -tln 2>/dev/null | grep -q ":${port}[[:space:]]"; then
            log "INFO" "Port $port is now listening"
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    
    log "ERROR" "Port $port did not become available within ${timeout}s"
    return 1
}

start_mt5linux_bridge() {
    local cmd="wine python -m mt5linux --host 0.0.0.0 --port ${BRIDGE_PORT}"

    log "INFO" "Starting mt5linux bridge: $cmd"

    # Wine Python can crash with WinError 6 in detached mode when no TTY exists.
    # Wrap it with script(1) so it always gets a pseudo-terminal.
    if command -v script >/dev/null 2>&1; then
        script -q -c "${cmd}" "${BRIDGE_TTY_LOG}" &
    else
        log "WARN" "script(1) not found; starting bridge without PTY wrapper"
        ${cmd} &
    fi

    BRIDGE_PID=$!
    log "INFO" "mt5linux bridge started with PID: $BRIDGE_PID"
    
    # Wait for bridge to become available
    if ! wait_for_port "$BRIDGE_PORT" "$BRIDGE_STARTUP_TIMEOUT"; then
        return 1
    fi
    
    return 0
}

stop_stale_mt5linux() {
    log "INFO" "Cleaning up stale bridge processes..."
    
    # Ensure stale bridge processes from previous runs do not keep the TCP port busy.
    pkill -f "wine python -m mt5linux --host" 2>/dev/null || true
    pkill -f "mt5linux --host .* --port ${BRIDGE_PORT}" 2>/dev/null || true

    # If a listener still exists, give wineserver a chance to release it.
    if ss -tln 2>/dev/null | grep -q ":${BRIDGE_PORT}[[:space:]]"; then
        log "WARN" "Port ${BRIDGE_PORT} still in use, killing wineserver..."
        sudo -u "$TRADER_USER" bash -lc "wineserver -k 2>/dev/null || true" || true
        sleep 2
        
        # Force kill if still stuck
        fuser -k "${BRIDGE_PORT}/tcp" 2>/dev/null || true
    fi
    
    log "INFO" "Stale process cleanup complete"
}

start_xvfb() {
    local existing_pid
    local display_num_short="${DISPLAY_NUM#:}"

    log "INFO" "Setting up X display: $DISPLAY_NUM"
    
    # Reuse existing Xvfb if display is already active.
    existing_pid="$(pgrep -f "Xvfb ${DISPLAY_NUM}" 2>/dev/null | head -n1 || true)"
    if [ -n "${existing_pid}" ] && is_process_running "$existing_pid"; then
        XVFB_PID="${existing_pid}"
        export DISPLAY="${DISPLAY_NUM}"
        log "INFO" "Reusing existing Xvfb on ${DISPLAY_NUM} (PID: ${XVFB_PID})"
        return 0
    fi

    if [ -f "${XVFB_LOCK_FILE}" ]; then
        log "INFO" "Removing stale Xvfb lock file: $XVFB_LOCK_FILE"
        rm -f "${XVFB_LOCK_FILE}" 2>/dev/null || true
    fi

    log "INFO" "Starting new Xvfb on ${DISPLAY_NUM}..."
    Xvfb "${DISPLAY_NUM}" -screen 0 1280x1024x24 -ac -nolisten tcp &
    XVFB_PID=$!
    export DISPLAY="${DISPLAY_NUM}"

    if ! wait_for_process "$XVFB_PID" "Xvfb" 3; then
        return 1
    fi

    log "INFO" "Xvfb started successfully (PID: $XVFB_PID, DISPLAY: $DISPLAY)"
    return 0
}

# ── Find MT5 executable ────────────────────────────────────────────────────
find_mt5() {
    log "INFO" "Searching for MT5 terminal executable..."
    
    for path in "${MT5_PATHS[@]}"; do
        if [ -f "${path}" ]; then
            MT5_PATH="${path}"
            log "INFO" "Found MT5 at: ${MT5_PATH}"
            return 0
        fi
    done
    
    log "ERROR" "MetaTrader 5 terminal not found in any expected location:"
    for path in "${MT5_PATHS[@]}"; do
        log "ERROR" "  - ${path}"
    done
    return 1
}

check_mt5_running() {
    local timeout="$1"
    local elapsed=0
    
    log "INFO" "Waiting for MT5 terminal to initialize (${timeout}s timeout)..."
    
    while [ $elapsed -lt $timeout ]; do
        # Check if Wine has started any terminal processes
        if pgrep -f "wine.*python.*-m mt5" >/dev/null 2>&1 || \
           pgrep -f "terminal64.exe" >/dev/null 2>&1; then
            log "INFO" "MT5 terminal process detected"
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    
    log "WARN" "MT5 terminal process not detected after ${timeout}s"
    return 1
}

# ── Cleanup on exit ────────────────────────────────────────────────────────
cleanup() {
    log "INFO" "Initiating graceful shutdown..."
    
    # Kill bridge
    if [ -n "${BRIDGE_PID:-}" ]; then
        log "INFO" "Stopping mt5linux bridge (PID: $BRIDGE_PID)..."
        if is_process_running "$BRIDGE_PID"; then
            kill "$BRIDGE_PID" 2>/dev/null || true
            sleep 2
            if is_process_running "$BRIDGE_PID"; then
                log "WARN" "Bridge did not stop gracefully, force killing..."
                kill -9 "$BRIDGE_PID" 2>/dev/null || true
            fi
        fi
    fi
    
    # Kill MT5
    if [ -n "${MT5_PID:-}" ]; then
        log "INFO" "Stopping MT5 terminal (PID: $MT5_PID)..."
        if is_process_running "$MT5_PID"; then
            kill "$MT5_PID" 2>/dev/null || true
            sleep 2
            if is_process_running "$MT5_PID"; then
                log "WARN" "MT5 did not stop gracefully, force killing..."
                kill -9 "$MT5_PID" 2>/dev/null || true
            fi
        fi
    fi
    
    # Kill leftover Wine processes
    log "INFO" "Cleaning up Wine processes..."
    sudo -u "$TRADER_USER" bash -lc "wineserver -k 2>/dev/null || true" || true
    
    # Kill Xvfb
    if [ -n "${XVFB_PID:-}" ]; then
        log "INFO" "Stopping Xvfb (PID: $XVFB_PID)..."
        if is_process_running "$XVFB_PID"; then
            kill "$XVFB_PID" 2>/dev/null || true
            sleep 1
        fi
    fi
    
    log "INFO" "Cleanup complete. Bridge shutdown."
}

trap cleanup EXIT SIGTERM SIGINT

log "INFO" "========================================="
log "INFO" "AutoTrader MT5 Bridge Startup"
log "INFO" "========================================="
log "INFO" "Configuration:"
log "INFO" "  Trader user: $TRADER_USER"
log "INFO" "  Wine prefix: $WINE_PREFIX"
log "INFO" "  Display: $DISPLAY_NUM"
log "INFO" "  Bridge port: $BRIDGE_PORT"
log "INFO" "  Bridge TTY log: $BRIDGE_TTY_LOG"
log "INFO" "========================================="

# ── Step 1: Clean up stale processes ──────────────────────────────────────
log "INFO" "[STEP 1] Cleaning up stale processes..."
stop_stale_mt5linux

# ── Step 2: Start Xvfb ────────────────────────────────────────────────────
log "INFO" "[STEP 2] Starting X display server..."
start_xvfb || error_exit "Failed to start Xvfb"

# ── Step 3: Find and start MT5 ────────────────────────────────────────────
log "INFO" "[STEP 3] Starting MetaTrader 5 terminal..."
find_mt5 || error_exit "MT5 terminal not found"

# ════════════════════════════════════════════════════════════════════════════
# CRITICAL: All Wine operations must run as 'trader' user
# ════════════════════════════════════════════════════════════════════════════
log "INFO" "[STEP 4] Launching all Wine/mt5linux services as trader user..."

export DISPLAY="${DISPLAY_NUM}"

# Start MT5 and mt5linux bridge in a single bash context as trader
# This ensures they share the same display and environment
sudo -u "$TRADER_USER" bash << 'TRADER_SCRIPT_EOF'
export WINEPREFIX="/home/trader/.wine"
export WINEARCH="win64"
export DISPLAY=":99"

log() {
    echo "[MT5-BRIDGE] $(date '+%Y-%m-%d %H:%M:%S') [INFO] $*"
}

log "Starting MT5 terminal from trader user context..."
# Start MT5 with /portable flag for headless/scripting support
wine "/home/trader/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe" /portable 2>&1 | tee -a /tmp/mt5-terminal.log &
MT5_PID=$!

# Wait for MT5 to start and load
log "MT5 terminal started (PID: $MT5_PID)"
sleep 3

# Now start mt5linux bridge
log "Starting mt5linux bridge on port 18812..."
cmd="wine python -m mt5linux --host 0.0.0.0 --port 18812"

# Create and fix permissions on log file
mkdir -p /tmp 2>/dev/null || true
touch /tmp/mt5linux-bridge-18812.typescript 2>/dev/null || true
chmod 666 /tmp/mt5linux-bridge-18812.typescript 2>/dev/null || true

if command -v script >/dev/null 2>&1; then
    script -q -c "$cmd" /tmp/mt5linux-bridge-18812.typescript &
else
    $cmd &
fi
BRIDGE_PID=$!

log "mt5linux bridge started (PID: $BRIDGE_PID)"

# Wait for port to become available
log "Waiting for bridge port 18812 (20 second timeout)..."
TIMEOUT=20
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if ss -tln 2>/dev/null | grep -q ":18812[[:space:]]"; then
        log "✓ Bridge port 18812 is now listening"
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

# Keep the bridge process alive
log "Bridge is running. Monitoring process..."
wait $BRIDGE_PID || true

TRADER_SCRIPT_EOF

# If we get here, trader user script exited
log "ERROR" "Trader user subprocess exited unexpectedly"
error_exit "MT5 Bridge services crashed"
