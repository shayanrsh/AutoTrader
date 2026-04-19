#!/usr/bin/env python3
"""Test MT5 with extended RPC timeouts"""
import mt5linux
import os

# Increase rpyc timeout globally
os.environ['RPYC_TIMEOUT'] = '300'  # 5 minutes

def test_extended():
    """MT5 test with extended timeouts"""
    print("=" * 70)
    print("MT5 Extended Timeout Test")
    print("=" * 70)
    print(f"RPYC_TIMEOUT set to: 300 seconds")
    
    try:
        # Create connection with very long timeout
        print("\nConnecting to MT5 RPC bridge...")
        mt5 = mt5linux.MetaTrader5(timeout=180)  # 3 minute timeout
        print("✓ MT5 connection object created")
        
        # Try initialize WITHOUT credentials first
        print("\nTesting initialize() without credentials...")
        print("  (waiting up to 180 seconds for response...)")
        
        result = mt5.initialize()
        print(f"  Result: {result}")
        
        if result:
            print("\n✓✓✓ SUCCESS WITHOUT CREDENTIALS!")
            
            account_info = mt5.account_info()
            if account_info:
                print(f"  Account: {account_info.login} ({account_info.server})")
                return True
        else:
            error = mt5.last_error
            print(f"  Failed: {error}")
            
            # Try with credentials
            print("\nTesting initialize() WITH credentials...")
            print("  Account: 52877297, Server: Alpari-MT5-Demo")
            print("  (waiting up to 180 seconds...)")
            
            result = mt5.initialize(
                login=52877297,
                password="W&!g4UfCChiR9s@",
                server="Alpari-MT5-Demo"
            )
            print(f"  Result: {result}")
            
            if result:
                print("\n✓✓✓ SUCCESS WITH CREDENTIALS!")
                account_info = mt5.account_info()
                if account_info:
                    print(f"  Account: {account_info.login}")
                    return True
            else:
                print(f"  Failed: {mt5.last_error}")
                
    except Exception as e:
        print(f"\nException: {e}")
        import traceback
        traceback.print_exc()
        
    return False

if __name__ == "__main__":
    success = test_extended()
    print("\n" + "=" * 70)
    print("Extended timeout test", "PASSED" if success else "FAILED")
    print("=" * 70)
