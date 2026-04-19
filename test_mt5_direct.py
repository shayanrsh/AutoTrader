#!/usr/bin/env python3
"""Test using direct MetaTrader5 Python module (not via RPC)"""

def test_direct_mt5():
    """Try the Windows MT5 Python module directly"""
    print("=" * 70)
    print("Direct MT5 Python Module Test")
    print("=" * 70)
    
    try:
        # Try importing the MT5 module directly (if available on this system)
        print("\n1. Checking for direct MetaTrader5 import...")
        import MetaTrader5 as mt5
        print("   ✓ Direct import successful!")
        
        print("\n2. Calling initialize()...")
        result = mt5.initialize()
        print(f"   Result: {result}")
        
        if result:
            print("   ✓ Initialization successful!")
            
            print("\n3. Getting account info...")
            account = mt5.account_info()
            if account:
                print(f"   ✓ Account: {account.login}")
                print(f"     Balance: {account.balance} {account.currency}")
                return True
                
        return False
        
    except ImportError as e:
        print(f"   ✗ Cannot import MetaTrader5: {e}")
        print("   Note: This module is only available in Wine Windows environment")
        print("   For Linux, we must use mt5linux RPC bridge")
        return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_rpc_bridge_alt():
    """Alternative: Check if we can at least verify bridge connectivity"""
    print("\n" + "=" * 70)
    print("Alternative: RPC Bridge Connectivity Check")
    print("=" * 70)
    
    import socket
    import time
    
    print("\nChecking bridge port 18812...")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        result = s.connect_ex(('127.0.0.1', 18812))
        s.close()
        
        if result == 0:
            print("  ✓ Bridge port is ACCESSIBLE")
            return True
        else:
            print("  ✗ Bridge port is NOT accessible")
            return False
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False

if __name__ == "__main__":
    # First try direct MT5 (will fail on Linux but good to document attempt)
    success1 = test_direct_mt5()
    
    # Then verify bridge is at least accessible
    success2 = check_rpc_bridge_alt()
    
    print("\n" + "=" * 70)
    if success1:
        print("RESULT: Direct MT5 module working!")
    elif success2:
        print("RESULT: RPC Bridge accessible (MT5 RPC may need investigation)")
    else:
        print("RESULT: Neither direct MT5 nor bridge accessible")
    print("=" * 70)
