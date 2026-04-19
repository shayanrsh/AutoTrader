#!/usr/bin/env python3
"""Test MT5 with explicit login credentials"""
import sys
import mt5linux
import time

def test_login():
    """Test with explicit login credentials"""
    print("[TEST] MT5 Login Test")
    print("="*60)
    
    try:
        # Create connection
        print("1. Creating MT5 connection...")
        mt5 = mt5linux.MetaTrader5(timeout=60)
        print("   ✓ Connection object created")
        
        # Try initialize without parameters
        print("\n2. Attempting initialize() without parameters...")
        result = mt5.initialize()
        print(f"   Result: {result}")
        
        if not result:
            # Get error
            error = mt5.last_error
            print(f"   Error: {error}")
            
            # Try with login parameters
            print("\n3. Attempting initialize() with login credentials...")
            print("   Credentials: account=52877297, server=Alpari-MT5-Demo")
            result = mt5.initialize(
                login=52877297,
                password="W&!g4UfCChiR9s@",
                server="Alpari-MT5-Demo"
            )
            print(f"   Result: {result}")
            
            if result:
                print("   ✓ Login succeeded!")
                
                # Get account info
                print("\n4. Getting account info...")
                account = mt5.account_info()
                if account:
                    print(f"   Account: {account.login}")
                    print(f"   Name: {account.name}")
                    print(f"   Balance: {account.balance} {account.currency}")
                    print(f"   Server: {account.server}")
                    return True
            else:
                error = mt5.last_error
                print(f"   Error: {error}")
                return False
        else:
            print("   ✓ Initialize succeeded without credentials!")
            
            # Get account info
            print("\n3. Getting account info...")
            account = mt5.account_info()
            if account:
                print(f"   Account: {account.login}")
                print(f"   Name: {account.name}")
                print(f"   Balance: {account.balance} {account.currency}")
                return True
            return False
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_login()
    sys.exit(0 if success else 1)
