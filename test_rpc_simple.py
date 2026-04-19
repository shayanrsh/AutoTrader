#!/usr/bin/env python3
"""Simple RPC test to check mt5linux bridge connectivity"""
import socket
import sys
import time

def test_rpc_port():
    """Test if MT5 RPC port is accessible"""
    print("[TEST] Checking RPC port 18812...")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        result = s.connect_ex(('127.0.0.1', 18812))
        s.close()
        if result == 0:
            print("✓ Port 18812 is REACHABLE")
            return True
        else:
            print("✗ Port 18812 is NOT reachable")
            return False
    except Exception as e:
        print(f"✗ Port test failed: {e}")
        return False

def test_mt5_import():
    """Test if mt5linux module is working"""
    print("\n[TEST] Testing mt5linux module import...")
    try:
        import mt5linux
        print("✓ mt5linux module imported successfully")
        return True
    except Exception as e:
        print(f"✗ mt5linux import failed: {e}")
        return False

def test_basic_rpc():
    """Try a basic RPC call with longer timeout"""
    print("\n[TEST] Attempting basic MT5 operations (timeout: 180s)...")
    try:
        import mt5linux
        
        print("  - Connecting to MT5...")
        mt5 = mt5linux.MetaTrader5(timeout=60)
        
        print("  - Testing initialize() (120s timeout)...")
        result = mt5.initialize(timeout=120)
        print(f"    Initialize result: {result}")
        
        if result:
            print("  ✓ MT5 initialization successful!")
            
            print("  - Getting account info...")
            account_info = mt5.account_info()
            print(f"    Account info: {account_info}")
            
            mt5.shutdown()
            print("✓ All RPC operations successful!")
            return True
        else:
            print("✗ MT5 initialization returned False")
            return False
            
    except Exception as e:
        print(f"✗ Basic RPC test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("="*60)
    print("MT5Linux RPC Connectivity Test")
    print("="*60)
    
    if test_rpc_port():
        test_mt5_import()
        print("\n[START] Beginning RPC operations test (180 second timeout)...")
        test_basic_rpc()
    else:
        print("✗ Cannot reach RPC port - bridge may not be running")
        sys.exit(1)
