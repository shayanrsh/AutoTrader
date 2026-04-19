#!/usr/bin/env python3
"""Debug MT5 initialization issues"""
import mt5linux
import time
import subprocess

def check_mt5_window():
    """Check if MT5 window exists"""
    print("[DEBUG] Checking for MT5 window...")
    try:
        result = subprocess.run(
            "DISPLAY=:99 wmctrl -l 2>/dev/null | grep -i 'meta\\|terminal'",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.stdout:
            print(f"  Windows found:\n{result.stdout}")
        else:
            print("  No MT5 window found (may run headless)")
    except Exception as e:
        print(f"  Window check failed: {e}")

def check_mt5_process():
    """Check if MT5 process is actually running"""
    print("[DEBUG] Checking MT5 process status...")
    try:
        result = subprocess.run(
            "ps aux | grep terminal64 | grep -v grep",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.stdout:
            print(f"  Process running: YES")
            print(f"  {result.stdout.strip()[:100]}...")
        else:
            print("  Process running: NO - MT5 may have crashed")
    except Exception as e:
        print(f"  Process check failed: {e}")

def test_mt5_direct():
    """Try to initialize MT5 with debug output"""
    print("\n[DEBUG] Attempting MT5 direct initialization...")
    
    try:
        mt5 = mt5linux.MetaTrader5(timeout=60)
        print(f"  MT5 object created: {mt5}")
        print(f"  MT5 class: {type(mt5)}")
        print(f"  MT5 attributes: {dir(mt5)}")
        
        print("\n  Calling initialize()...")
        result = mt5.initialize()
        print(f"  Initialize returned: {result} (type: {type(result)})")
        
        if not result:
            # Try to get last error
            if hasattr(mt5, 'last_error'):
                print(f"  Last error: {mt5.last_error}")
            
            # Check if login is required
            print("\n  Trying initialize() with login...")
            result = mt5.initialize(login=12345, password="test", server="demo")
            print(f"  Initialize with login returned: {result}")
            
            if result:
                account_info = mt5.account_info()
                print(f"  Account info: {account_info}")
                
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()

def check_wine_display():
    """Check if Wine can access display"""
    print("[DEBUG] Checking Wine/X11 display setup...")
    try:
        result = subprocess.run(
            "DISPLAY=:99 xdpyinfo 2>&1 | head -5",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )
        if "display" in result.stdout.lower():
            print("  ✓ Display :99 is accessible")
        else:
            print(f"  ✗ Display check failed: {result.stderr}")
    except Exception as e:
        print(f"  Display check error: {e}")

if __name__ == "__main__":
    print("="*60)
    print("MT5 Initialization Debugging")
    print("="*60)
    
    check_wine_display()
    check_mt5_process()
    check_mt5_window()
    test_mt5_direct()
