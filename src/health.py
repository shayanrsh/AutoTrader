"""
AutoTrader — Health Check HTTP Server

Lightweight aiohttp server providing:
  /health — JSON status for monitoring tools
  /metrics — Basic operational statistics
"""

from __future__ import annotations

import time
from typing import Optional

from aiohttp import web

from src.models import HealthStatus
from src.utils import get_logger

logger = get_logger("health")


class HealthCheckServer:
    """
    Minimal HTTP server for external health monitoring.
    Runs on a configurable port (default: 8080).
    """

    def __init__(self, port: int = 8080) -> None:
        self._port = port
        self._start_time = time.monotonic()
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

        # Mutable state updated by the main orchestrator
        self.last_signal_time: Optional[str] = None
        self.open_trades: int = 0
        self.daily_pnl: float = 0.0
        self.dry_run: bool = True
        self.mt5_connected: bool = False
        self.telegram_connected: bool = False
        self.signals_processed: int = 0
        self.trades_executed: int = 0
        self.errors_count: int = 0
        self.parser_stats: dict = {}

    async def start(self) -> None:
        """Start the health check HTTP server."""
        if self._port <= 0:
            logger.info("Health check server disabled (port=0)")
            return

        self._app = web.Application()
        self._app.add_routes([
            web.get("/health", self._handle_health),
            web.get("/metrics", self._handle_metrics),
            web.get("/", self._handle_root),
        ])

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        logger.info("Health check server started on port %d", self._port)

    async def stop(self) -> None:
        """Stop the health check HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Health check server stopped")

    async def _handle_root(self, request: web.Request) -> web.Response:
        """Root endpoint — redirect to /health."""
        return web.json_response({"message": "AutoTrader v1.0.0", "endpoints": ["/health", "/metrics"]})

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        uptime = time.monotonic() - self._start_time
        status = HealthStatus(
            status="ok" if self.mt5_connected else "degraded",
            uptime_seconds=round(uptime, 1),
            last_signal_time=self.last_signal_time,
            open_trades=self.open_trades,
            daily_pnl=self.daily_pnl,
            dry_run=self.dry_run,
            mt5_connected=self.mt5_connected,
            telegram_connected=self.telegram_connected,
        )
        return web.json_response(status.model_dump())

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Operational metrics endpoint."""
        uptime = time.monotonic() - self._start_time
        metrics = {
            "uptime_seconds": round(uptime, 1),
            "signals_processed": self.signals_processed,
            "trades_executed": self.trades_executed,
            "errors_count": self.errors_count,
            "open_trades": self.open_trades,
            "daily_pnl": self.daily_pnl,
            "parser_stats": self.parser_stats,
            "mt5_connected": self.mt5_connected,
            "telegram_connected": self.telegram_connected,
            "dry_run": self.dry_run,
        }
        return web.json_response(metrics)
