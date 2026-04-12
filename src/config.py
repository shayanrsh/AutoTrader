"""
AutoTrader — Configuration Module

Loads and validates all configuration from environment variables / .env file.
Uses pydantic-settings for type coercion and validation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """Return the project root directory (parent of src/)."""
    return Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """
    All application settings loaded from environment variables.
    Defaults are sane for development; production values come from config.env.
    """

    model_config = SettingsConfigDict(
        env_file=str(_project_root() / "config.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram Listener ──────────────────────────────────────────────
    telegram_api_id: int = Field(..., description="Telegram API ID from my.telegram.org")
    telegram_api_hash: str = Field(..., description="Telegram API hash from my.telegram.org")
    telegram_phone: str = Field(..., description="Phone number in international format")
    telegram_channel_id: str = Field(
        ..., description="Numeric channel ID (negative) or channel username"
    )
    telegram_session_path: str = Field(
        "data/autotrader.session",
        description="Path to Telethon session file",
    )

    # ── Notification Bot ───────────────────────────────────────────────
    notify_bot_token: str = Field(..., description="Telegram bot token from @BotFather")
    notify_chat_id: int = Field(..., description="Your personal chat ID for alerts")

    # ── AI Parser: Gemini ──────────────────────────────────────────────
    gemini_api_key: str = Field(..., description="Google Gemini API key")
    gemini_model: str = Field(
        "gemini-2.0-flash", description="Gemini model name"
    )

    # ── AI Parser: Groq ───────────────────────────────────────────────
    groq_api_key: str = Field(..., description="Groq API key")
    groq_model: str = Field(
        "llama-3.3-70b-versatile", description="Groq model name"
    )

    # ── MT5 Connection ────────────────────────────────────────────────
    mt5_host: str = Field("localhost", description="mt5linux bridge host")
    mt5_port: int = Field(18812, description="mt5linux bridge port")
    mt5_account: int = Field(..., description="MT5 account number")
    mt5_password: str = Field(..., description="MT5 account password")
    mt5_server: str = Field("Alpari-MT5", description="MT5 broker server name")

    # ── Trading Parameters ────────────────────────────────────────────
    symbol: str = Field("XAUUSD", description="Trading symbol base name")
    magic_number: int = Field(240001, description="Unique EA magic number")
    default_lot_size: float = Field(0.01, ge=0.01, description="Default lot size")
    max_lot_size: float = Field(1.0, ge=0.01, description="Max lot size per order")
    min_lot_size: float = Field(0.01, ge=0.01, description="Min lot size")
    lot_step: float = Field(0.01, ge=0.01, description="Lot size increment")
    max_slippage_points: int = Field(
        50, ge=0, description="Max slippage in points"
    )

    # ── Risk Management ───────────────────────────────────────────────
    max_risk_per_trade_pct: float = Field(
        1.0, ge=0.1, le=10.0, description="Max risk per trade (%)"
    )
    max_open_trades: int = Field(5, ge=1, le=50, description="Max concurrent positions")
    daily_loss_limit_pct: float = Field(
        5.0, ge=0.5, le=50.0, description="Daily loss limit (%)"
    )
    signal_expiry_minutes: int = Field(
        30, ge=1, description="Discard signals older than this"
    )
    dedup_window_hours: int = Field(
        4, ge=1, description="Deduplication window (hours)"
    )

    # ── Operation ─────────────────────────────────────────────────────
    dry_run: bool = Field(True, description="If true, log actions but don't trade")
    log_level: str = Field("INFO", description="Logging level")
    log_file_path: str = Field("data/autotrader.log", description="Log file path")
    log_max_size_mb: int = Field(10, ge=1, description="Max log file size (MB)")
    log_backup_count: int = Field(5, ge=1, description="Number of rotated logs to keep")
    database_path: str = Field("data/autotrader.db", description="SQLite database path")
    health_check_port: int = Field(
        8080, ge=0, le=65535, description="Health check port (0 = disabled)"
    )

    # ── Validators ────────────────────────────────────────────────────
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got '{v}'")
        return upper

    @field_validator("telegram_session_path", "log_file_path", "database_path")
    @classmethod
    def resolve_relative_paths(cls, v: str) -> str:
        """Convert relative paths to absolute paths based on project root."""
        p = Path(v)
        if not p.is_absolute():
            p = _project_root() / p
        # Ensure parent directory exists
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    @field_validator("telegram_api_hash")
    @classmethod
    def validate_api_hash(cls, v: str) -> str:
        if len(v) != 32:
            raise ValueError(
                "telegram_api_hash must be a 32-character hex string. "
                "Get yours from https://my.telegram.org/apps"
            )
        return v

    @field_validator("telegram_channel_id")
    @classmethod
    def validate_channel_id(cls, v: str) -> str:
        value = str(v).strip()
        if not value:
            raise ValueError("telegram_channel_id cannot be empty")

        if value.startswith("@"):
            value = value[1:]

        if value.lstrip("-").isdigit():
            return value

        if not value.replace("_", "").isalnum():
            raise ValueError(
                "telegram_channel_id must be a numeric id (e.g. -100123...) "
                "or a valid username"
            )
        return value


# ── Singleton access ─────────────────────────────────────────────────────

_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """
    Return the singleton Settings instance.
    Loaded once on first call; subsequent calls return the cached instance.
    """
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings


def reload_settings() -> Settings:
    """Force-reload settings from environment (useful for testing)."""
    global _settings
    _settings = Settings()  # type: ignore[call-arg]
    return _settings
