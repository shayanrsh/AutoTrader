"""
AutoTrader — Utility Functions

Logging setup, sensitive-value masking, hashing, and timestamp helpers.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


# ── Sensitive value masking ─────────────────────────────────────────────────

# Patterns that look like API keys, passwords, or tokens
_SENSITIVE_PATTERNS = [
    re.compile(r"(api[_-]?key|api[_-]?hash|password|token|secret)\s*[=:]\s*\S+", re.I),
    re.compile(r"AIzaSy[\w-]{33}"),             # Google API key
    re.compile(r"gsk_[a-zA-Z0-9]{52,}"),         # Groq API key
    re.compile(r"\d{7,}:[A-Za-z0-9_-]{35,}"),    # Telegram bot token
]


def mask_sensitive(text: str) -> str:
    """Replace sensitive values in text with [REDACTED]."""
    result = text
    for pattern in _SENSITIVE_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


class SensitiveFilter(logging.Filter):
    """Logging filter that masks sensitive values in log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_sensitive(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: mask_sensitive(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    mask_sensitive(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


# ── Logging setup ───────────────────────────────────────────────────────────

_logger_initialized = False


def setup_logging(
    log_level: str = "INFO",
    log_file_path: Optional[str] = None,
    max_size_mb: int = 10,
    backup_count: int = 5,
) -> logging.Logger:
    """
    Configure the root logger with console + rotating file output.

    Args:
        log_level: Logging level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file_path: Path to log file. None = console only.
        max_size_mb: Max log file size before rotation.
        backup_count: Number of rotated files to keep.

    Returns:
        The configured root logger.
    """
    global _logger_initialized

    logger = logging.getLogger("autotrader")

    if _logger_initialized:
        return logger

    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False

    # Formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(SensitiveFilter())
    logger.addHandler(console_handler)

    # File handler (if path provided)
    if log_file_path:
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=str(log_path),
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(SensitiveFilter())
        logger.addHandler(file_handler)

    _logger_initialized = True
    logger.info("Logging initialized — level=%s, file=%s", log_level, log_file_path or "none")
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the autotrader namespace."""
    return logging.getLogger(f"autotrader.{name}")


# ── Hashing ─────────────────────────────────────────────────────────────────


def signal_hash(action: str, entry: float, sl: float, tp: float) -> str:
    """
    Generate a short deduplication hash for a trading signal.

    Args:
        action: BUY or SELL
        entry: Entry price
        sl: Stop-loss price
        tp: First take-profit price

    Returns:
        16-character hex digest.
    """
    key = f"{action}|{entry:.2f}|{sl:.2f}|{tp:.2f}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Timestamp utilities ─────────────────────────────────────────────────────


def utc_now() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def timestamp_age_minutes(ts: datetime) -> float:
    """Return how many minutes old a timestamp is (relative to now UTC)."""
    now = utc_now()
    if ts.tzinfo is None:
        # Treat naive datetimes as UTC
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    return delta.total_seconds() / 60.0


def format_timestamp(ts: datetime) -> str:
    """Format a datetime for display in log messages and notifications."""
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


# ── Price formatting ────────────────────────────────────────────────────────


def format_price(price: float, digits: int = 2) -> str:
    """Format a price to the specified number of decimal places."""
    return f"{price:.{digits}f}"


def format_lot(lot: float) -> str:
    """Format a lot size to 2 decimal places."""
    return f"{lot:.2f}"


def format_pct(pct: float) -> str:
    """Format a percentage to 2 decimal places with % sign."""
    return f"{pct:.2f}%"
