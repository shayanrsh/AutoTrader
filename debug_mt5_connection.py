#!/usr/bin/env python3
"""
Debug script to test MT5 bridge connection step-by-step
"""
import asyncio
import sys
import time
import socket
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.utils import get_logger
from src.config import get_settings

logger = get_logger("debug_mt5")


def test_port_connectivity(host: str, port: int, timeout: float = 5.0) -> bool:
    """Test if we can connect to the bridge port."""
    logger.info(f"Testing port {host}:{port} connectivity...")
    try:
        with socket.create_connection((host, port), timeout=timeout):
            logger.info(f"✓ Port {port} is reachable")
            return True
    except (socket.timeout, socket.error) as e:
        logger.error(f"✗ Port {port} unreachable: {e}")
        return False


async def test_bridge_connection(host: str, port: int) -> bool:
    """Test if we can import and connect to mt5linux."""
    logger.info(f"Testing mt5linux import and bridge connection...")
    
    try:
        from mt5linux import MetaTrader5
        logger.info("✓ mt5linux imported successfully")
    except ImportError as e:
        logger.error(f"✗ Failed to import mt5linux: {e}")
        logger.info("  Install with: pip install mt5linux")
        return False
    
    try:
        logger.info(f"Creating MetaTrader5 client (host={host}, port={port})...")
        mt5 = await asyncio.wait_for(
            asyncio.to_thread(
                MetaTrader5,
                host=host,
                port=port,
                timeout=30,
            ),
            timeout=35,
        )
        logger.info("✓ MetaTrader5 client created")
        
        logger.info("Testing initialize()...")
        init_result = await asyncio.wait_for(
            asyncio.to_thread(mt5.initialize),
            timeout=30,
        )
        
        if not init_result:
            logger.error("✗ initialize() returned False")
            return False
        
        logger.info("✓ initialize() succeeded")
        
        logger.info("Getting terminal info...")
        terminal_info = await asyncio.wait_for(
            asyncio.to_thread(mt5.terminal_info),
            timeout=10,
        )
        
        if terminal_info:
            logger.info(f"✓ Terminal info: {terminal_info}")
        else:
            logger.error("✗ terminal_info() returned None")
        
        logger.info("Getting account info...")
        account_info = await asyncio.wait_for(
            asyncio.to_thread(mt5.account_info),
            timeout=10,
        )
        
        if account_info:
            logger.info(f"✓ Account info: login={account_info.login}, server={account_info.server}")
        else:
            logger.error("✗ account_info() returned None")
        
        logger.info("Shutting down...")
        mt5.shutdown()
        logger.info("✓ Connection test successful!")
        return True
        
    except asyncio.TimeoutError as e:
        logger.error(f"✗ Timeout during MT5 operations: {e}")
        return False
    except Exception as e:
        logger.error(f"✗ Connection error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    logger.info("=" * 70)
    logger.info("MT5 Bridge Connection Debug")
    logger.info("=" * 70)
    
    settings = get_settings()
    
    # Test 1: Port connectivity
    logger.info("\n[Test 1] Port Connectivity")
    if not test_port_connectivity(settings.mt5_host, settings.mt5_port):
        logger.error("Bridge port not responding. Start the bridge first:")
        logger.error("  systemctl start mt5-bridge")
        logger.error("  or")
        logger.error("  /home/trader/autotrader/systemd/start_mt5_bridge.sh")
        return 1
    
    # Wait a bit to ensure bridge is ready
    await asyncio.sleep(2)
    
    # Test 2: Bridge connection
    logger.info("\n[Test 2] MT5 Bridge Connection & Initialization")
    if not await test_bridge_connection(settings.mt5_host, settings.mt5_port):
        logger.error("Bridge connection test failed")
        return 1
    
    logger.info("\n" + "=" * 70)
    logger.info("✓ All tests passed!")
    logger.info("=" * 70)
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
