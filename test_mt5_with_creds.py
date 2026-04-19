#!/usr/bin/env python3
"""Test MT5 initialization with explicit credentials"""
import mt5linux
import time

def test_with_credentials():
    """Try MT5 with explicit login"""
    print("=" * 70)
    print("MT5 Connection Test WITH Credentials")
    print("=" * 70)
    
    credentials = {
        "account": 52877297,
        "password": "W&!g4UfCChiR9s@",
        "server": "Alpari-MT5-Demo"
    }
    
    print(f"\nConnecting to MT5...")
    print(f"  Account: {credentials['account']}")
    print(f"  Server: {credentials['server']}")
    
    try:
        # Create connection
        mt5 = mt5linux.MetaTrader5(timeout=60)
        print("✓ MT5 object created")
        
        # Try initialize WITH credentials
        print("\nCalling initialize() WITH login credentials...")
        result = mt5.initialize(
            login=credentials["account"],
            password=credentials["password"],
            server=credentials["server"]
        )
        
        print(f"  Initialize result: {result}")
        
        if result:
            print("\n✓✓✓ SUCCESS! MT5 initialized with credentials!")
            
            # Get account info
            account_info = mt5.account_info()
            if account_info:
                print(f"\n  Account Login: {account_info.login}")
                print(f"  Account Name: {account_info.name}")
                print(f"  Balance: {account_info.balance} {account_info.currency}")
                print(f"  Server: {account_info.server}")
                print(f"  Leverage: 1:{account_info.leverage}")
                
                # Try getting a symbol
                print("\n  Testing symbol info retrieval...")
                symbol = mt5.symbol_info("XAUUSD")
                if symbol:
                    print(f"    ✓ XAUUSD found: bid={symbol.bid}, ask={symbol.ask}")
                else:
                    # Try variant
                    symbol = mt5.symbol_info("XAUUSDm")
                    if symbol:
                        print(f"    ✓ XAUUSDm found: bid={symbol.bid}, ask={symbol.ask}")
                    else:
                        print("    ✗ XAUUSD/XAUUSDm not found")
                
                mt5.shutdown()
                return True
            else:
                print("✗ account_info returned None")
                return False
        else:
            error = mt5.last_error
            print(f"  Initialize failed: {error}")
            return False
            
    except Exception as e:
        print(f"\n✗✗✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_with_credentials()
    print("\n" + "=" * 70)
    if success:
        print("RESULT: SUCCESS - MT5 is fully operational!")
    else:
        print("RESULT: FAILED - MT5 initialization or login unsuccessful")
    print("=" * 70)
