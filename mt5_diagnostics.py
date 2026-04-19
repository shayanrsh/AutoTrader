#!/usr/bin/env python3
"""
MT5 Setup Diagnostic Tool
Checks all components of the MT5 bridge infrastructure
"""
import subprocess
import socket
import os
import sys
from pathlib import Path

def check_process(name, grep_pattern):
    """Check if a process is running"""
    try:
        result = subprocess.run(
            f"ps aux | grep -E '{grep_pattern}' | grep -v grep | wc -l",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )
        count = int(result.stdout.strip())
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {name}: {count} process(es)")
        return count > 0
    except Exception as e:
        print(f"  ✗ {name}: Error - {e}")
        return False

def check_port(port, host="127.0.0.1"):
    """Check if a port is listening"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex((host, port))
        s.close()
        status = "✓" if result == 0 else "✗"
        print(f"  {status} Port {port}: {'LISTENING' if result == 0 else 'NOT LISTENING'}")
        return result == 0
    except Exception as e:
        print(f"  ✗ Port {port}: Error - {e}")
        return False

def check_file(path, description):
    """Check if a file/directory exists"""
    exists = Path(path).exists()
    status = "✓" if exists else "✗"
    print(f"  {status} {description}: {path}")
    return exists

def check_env(var_name):
    """Check environment variable"""
    value = os.environ.get(var_name, "")
    status = "✓" if value else "✗"
    print(f"  {status} ${var_name}: {value if value else '(not set)'}")
    return bool(value)

def run_diagnostics():
    """Run all diagnostic checks"""
    print("\n" + "=" * 70)
    print("MT5 BRIDGE INFRASTRUCTURE DIAGNOSTIC")
    print("=" * 70)

    all_ok = True

    # 1. Environment Variables
    print("\n[1] Environment Configuration")
    wine_prefix_ok = check_env("WINEPREFIX")
    display_ok = check_env("DISPLAY")
    all_ok = all_ok and wine_prefix_ok and display_ok

    # 2. Wine Setup
    print("\n[2] Wine & User Setup")
    wine_files_ok = check_file("/home/trader/.wine", "WINEPREFIX")
    mt5_binary_ok = check_file(
        "/home/trader/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe",
        "MT5 Binary"
    )
    python_ok = check_file(
        "/home/trader/.wine/drive_c/users/trader/AppData/Local/Programs/Python/Python311",
        "Python 3.11 (Wine)"
    )
    all_ok = all_ok and wine_files_ok and mt5_binary_ok and python_ok

    # 3. Processes
    print("\n[3] Running Processes")
    xvfb_ok = check_process("Xvfb Display", "Xvfb.*:99")
    mt5_ok = check_process("MT5 Terminal", "terminal64")
    mt5linux_ok = check_process("mt5linux Bridge", "mt5linux|python.*mt5")
    all_ok = all_ok and xvfb_ok and mt5_ok and mt5linux_ok

    # 4. Network
    print("\n[4] Network / RPC Bridge")
    bridge_port_ok = check_port(18812)
    all_ok = all_ok and bridge_port_ok

    # 5. Python Modules
    print("\n[5] Python Modules")
    try:
        import mt5linux
        print("  ✓ mt5linux: Installed")
        mt5linux_mod_ok = True
    except ImportError:
        print("  ✗ mt5linux: Not installed")
        mt5linux_mod_ok = False
    all_ok = all_ok and mt5linux_mod_ok

    # 6. Log Files
    print("\n[6] Log Files")
    check_file("/tmp/mt5-terminal.log", "MT5 Terminal Log")
    check_file("/tmp/mt5linux-bridge-18812.typescript", "mt5linux Bridge Log")

    # 7. Summary
    print("\n" + "=" * 70)
    if all_ok:
        print("STATUS: ✓✓✓ All checks PASSED - Infrastructure is healthy")
        print("\nNote: If MT5 initialization still times out:")
        print("  1. Check /tmp/mt5-terminal.log for Wine/MT5 errors")
        print("  2. Verify Alpari account credentials in config.env")
        print("  3. Check network connectivity from container to broker")
        print("  4. Try manual GUI login via DISPLAY=:99 before RPC")
    else:
        print("STATUS: ✗✗✗ Some checks FAILED - See details above")
        print("\nTo fix:")
        print("  1. Check systemd service: systemctl status mt5-bridge")
        print("  2. Restart bridge: sudo systemctl restart mt5-bridge")
        print("  3. Check logs: tail -f /tmp/mt5-terminal.log")
    print("=" * 70 + "\n")

    return all_ok

if __name__ == "__main__":
    success = run_diagnostics()
    sys.exit(0 if success else 1)
