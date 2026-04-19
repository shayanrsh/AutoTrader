"""
AutoTrader — Database Module

Async SQLite operations for signal logging, trade tracking, deduplication,
and daily P&L aggregation. Uses aiosqlite for non-blocking I/O.
"""

from __future__ import annotations

import sys
import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from src.models import SignalRecord, TradeResult
    from src.utils import get_logger, utc_now
except ModuleNotFoundError:
    # Supports direct execution from the src/ directory (python3 database.py).
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from src.models import SignalRecord, TradeResult
    from src.utils import get_logger, utc_now

logger = get_logger("database")

# ── SQL Schema ──────────────────────────────────────────────────────────────

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_hash      TEXT    NOT NULL,
    raw_text        TEXT    NOT NULL,
    parsed_action   TEXT,
    parsed_entry    REAL,
    parsed_sl       REAL,
    parsed_tp1      REAL,
    parser_source   TEXT    DEFAULT 'unknown',
    trade_ticket    INTEGER,
    trade_status    TEXT,
    message_id      INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_signals_hash ON signals(dedup_hash);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS telegram_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    channel_id      TEXT,
    event_type      TEXT NOT NULL,
    text_before     TEXT,
    text_after      TEXT,
    parse_status    TEXT NOT NULL DEFAULT 'PENDING',
    parsed_at       TEXT,
    parser_source   TEXT,
    parse_error     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tg_messages_id ON telegram_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_tg_messages_type ON telegram_messages(event_type);
CREATE INDEX IF NOT EXISTS idx_tg_messages_created ON telegram_messages(created_at);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ticket    INTEGER,
    symbol          TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    volume          REAL    NOT NULL,
    price           REAL,
    stop_loss       REAL    NOT NULL,
    take_profit     REAL    NOT NULL,
    status          TEXT    NOT NULL,
    error_code      INTEGER,
    error_message   TEXT,
    signal_hash     TEXT    NOT NULL,
    retries         INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_signal ON trades(signal_hash);

CREATE TABLE IF NOT EXISTS daily_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT    NOT NULL UNIQUE,
    starting_balance REAL,
    ending_balance  REAL,
    total_pnl       REAL    DEFAULT 0.0,
    trades_opened   INTEGER DEFAULT 0,
    trades_closed   INTEGER DEFAULT 0,
    signals_received INTEGER DEFAULT 0,
    signals_skipped INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_stats(trade_date);
"""


class Database:
    """Async SQLite database wrapper for AutoTrader."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Open database connection and create tables if needed."""
        db_file = Path(self._db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(str(db_file))
        self._conn.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrent read performance
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        # Create tables
        await self._conn.executescript(_CREATE_TABLES_SQL)
        await self._ensure_telegram_messages_columns()
        await self._conn.commit()
        logger.info("Database connected: %s", self._db_path)

    async def _ensure_telegram_messages_columns(self) -> None:
        """Apply additive migrations for telegram_messages parse tracking columns."""
        cursor = await self.conn.execute("PRAGMA table_info(telegram_messages)")
        rows = await cursor.fetchall()
        columns = {str(row["name"]) for row in rows}

        migrations: list[str] = []
        if "parse_status" not in columns:
            migrations.append(
                "ALTER TABLE telegram_messages "
                "ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'PENDING'"
            )
        if "parsed_at" not in columns:
            migrations.append("ALTER TABLE telegram_messages ADD COLUMN parsed_at TEXT")
        if "parser_source" not in columns:
            migrations.append("ALTER TABLE telegram_messages ADD COLUMN parser_source TEXT")
        if "parse_error" not in columns:
            migrations.append("ALTER TABLE telegram_messages ADD COLUMN parse_error TEXT")

        for stmt in migrations:
            await self.conn.execute(stmt)

        if migrations:
            logger.info("Applied telegram_messages migration(s): %d", len(migrations))

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    # ── Signal Operations ───────────────────────────────────────────────

    async def insert_signal(self, record: SignalRecord) -> int:
        """
        Insert a signal record and return the new row ID.
        """
        cursor = await self.conn.execute(
            """
            INSERT INTO signals
                (dedup_hash, raw_text, parsed_action, parsed_entry,
                 parsed_sl, parsed_tp1, parser_source, trade_ticket,
                 trade_status, message_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.dedup_hash,
                record.raw_text,
                record.parsed_action,
                record.parsed_entry,
                record.parsed_sl,
                record.parsed_tp1,
                record.parser_source,
                record.trade_ticket,
                record.trade_status,
                record.message_id,
                record.created_at.isoformat(),
            ),
        )
        await self.conn.commit()
        row_id = cursor.lastrowid
        logger.debug("Inserted signal record id=%d hash=%s", row_id, record.dedup_hash)
        return row_id  # type: ignore[return-value]

    async def insert_telegram_message_event(
        self,
        message_id: int,
        event_type: str,
        channel_id: Optional[str] = None,
        text_before: Optional[str] = None,
        text_after: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> int:
        """Insert a Telegram message event row (new/edit/delete)."""
        existing = await self.conn.execute(
            """
                        SELECT id
            FROM telegram_messages
            WHERE message_id = ?
              AND event_type = ?
              AND COALESCE(channel_id, '') = COALESCE(?, '')
              AND COALESCE(text_before, '') = COALESCE(?, '')
              AND COALESCE(text_after, '') = COALESCE(?, '')
            ORDER BY id DESC
            LIMIT 1
            """,
            (message_id, event_type, channel_id, text_before, text_after),
        )
        row = await existing.fetchone()
        if row is not None:
            return int(row["id"])

        ts = (created_at or utc_now()).isoformat()
        cursor = await self.conn.execute(
            """
            INSERT INTO telegram_messages
                (message_id, channel_id, event_type, text_before, text_after, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, channel_id, event_type, text_before, text_after, ts),
        )
        await self.conn.commit()
        row_id = cursor.lastrowid
        logger.debug(
            "Inserted telegram event id=%d message_id=%s type=%s",
            row_id,
            message_id,
            event_type,
        )
        return row_id  # type: ignore[return-value]

    async def get_telegram_event_parse_status(self, event_id: int) -> Optional[str]:
        """Return parse status for a telegram_messages row id."""
        cursor = await self.conn.execute(
            "SELECT parse_status FROM telegram_messages WHERE id = ?",
            (event_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return str(row["parse_status"]) if row["parse_status"] is not None else None

    async def mark_telegram_event_parse_status(
        self,
        event_id: int,
        status: str,
        parser_source: Optional[str] = None,
        parse_error: Optional[str] = None,
    ) -> None:
        """Update parse status metadata for a telegram_messages row."""
        normalized_status = status.strip().upper()
        parsed_at = utc_now().isoformat() if normalized_status in {"PROCESSED", "SKIPPED", "FAILED"} else None
        await self.conn.execute(
            """
            UPDATE telegram_messages
            SET parse_status = ?, parsed_at = ?, parser_source = ?, parse_error = ?
            WHERE id = ?
            """,
            (
                normalized_status,
                parsed_at,
                parser_source,
                parse_error,
                event_id,
            ),
        )
        await self.conn.commit()

    async def get_pending_telegram_events(
        self,
        limit: int = 200,
        include_failed: bool = False,
    ) -> list[dict]:
        """Return telegram message events pending AI parsing."""
        statuses = ["PENDING"]
        if include_failed:
            statuses.append("FAILED")

        placeholders = ",".join("?" for _ in statuses)
        cursor = await self.conn.execute(
            f"""
            SELECT id, message_id, event_type, channel_id, text_after, created_at, parse_status
            FROM telegram_messages
            WHERE parse_status IN ({placeholders})
              AND event_type != 'delete'
              AND COALESCE(text_after, '') != ''
            ORDER BY id ASC
            LIMIT ?
            """,
            tuple(statuses + [max(1, int(limit))]),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def has_parsed_signal_for_message(self, message_id: int) -> bool:
        """Check if a parsed trading signal already exists for a message id."""
        cursor = await self.conn.execute(
            """
            SELECT 1
            FROM signals
            WHERE message_id = ?
              AND COALESCE(parsed_action, '') != ''
            LIMIT 1
            """,
            (message_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def has_skipped_signal_for_message(self, message_id: int) -> bool:
        """Check if a non-signal SKIPPED record already exists for a message id."""
        cursor = await self.conn.execute(
            """
            SELECT 1
            FROM signals
            WHERE message_id = ?
              AND COALESCE(trade_status, '') = 'SKIPPED'
              AND COALESCE(parsed_action, '') = ''
            LIMIT 1
            """,
            (message_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def recover_processing_telegram_events(self) -> int:
        """Recover stale PROCESSING telegram rows after interrupted runs."""
        cursor = await self.conn.execute(
            """
            UPDATE telegram_messages
            SET parse_status = 'PENDING',
                parser_source = COALESCE(parser_source, 'none'),
                parse_error = CASE
                    WHEN COALESCE(parse_error, '') = '' THEN 'recovered from stale PROCESSING state'
                    ELSE parse_error
                END
            WHERE parse_status = 'PROCESSING'
            """
        )
        updated = cursor.rowcount
        await self.conn.commit()
        return updated

    async def is_duplicate(self, dedup_hash: str, window_hours: int = 4) -> bool:
        """
        Check if a signal with the same hash exists within the dedup window.

        Args:
            dedup_hash: The signal's deduplication hash.
            window_hours: How far back to look for duplicates.

        Returns:
            True if a matching signal exists within the window.
        """
        cutoff = (utc_now() - timedelta(hours=window_hours)).isoformat()
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) as cnt
            FROM signals
            WHERE dedup_hash = ? AND created_at >= ?
            """,
            (dedup_hash, cutoff),
        )
        row = await cursor.fetchone()
        count = row["cnt"] if row else 0
        if count > 0:
            logger.info(
                "Duplicate signal detected: hash=%s (found %d within %dh)",
                dedup_hash, count, window_hours,
            )
        return count > 0

    async def update_signal_trade(
        self, signal_id: int, ticket: Optional[int], status: str
    ) -> None:
        """Update a signal record with the trade execution result."""
        await self.conn.execute(
            """
            UPDATE signals SET trade_ticket = ?, trade_status = ?
            WHERE id = ?
            """,
            (ticket, status, signal_id),
        )
        await self.conn.commit()

    # ── Trade Operations ────────────────────────────────────────────────

    async def insert_trade(self, result: TradeResult) -> int:
        """Insert a trade execution record."""
        cursor = await self.conn.execute(
            """
            INSERT INTO trades
                (order_ticket, symbol, action, volume, price,
                 stop_loss, take_profit, status, error_code,
                 error_message, signal_hash, retries, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.order_ticket,
                result.symbol,
                result.action.value,
                result.volume,
                result.price,
                result.stop_loss,
                result.take_profit,
                result.status.value,
                result.error_code,
                result.error_message,
                result.signal_hash,
                result.retries,
                result.timestamp.isoformat(),
            ),
        )
        await self.conn.commit()
        row_id = cursor.lastrowid
        logger.debug("Inserted trade record id=%d ticket=%s", row_id, result.order_ticket)
        return row_id  # type: ignore[return-value]

    # ── Daily Stats ─────────────────────────────────────────────────────

    async def get_today_trade_count(self) -> int:
        """Get the number of trades opened today."""
        today = utc_now().strftime("%Y-%m-%d")
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) as cnt FROM trades
            WHERE created_at >= ? AND status = 'SUCCESS'
            """,
            (today,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def get_today_signals_count(self) -> int:
        """Get the number of signals received today."""
        today = utc_now().strftime("%Y-%m-%d")
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) as cnt FROM signals
            WHERE created_at >= ?
            """,
            (today,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def record_daily_pnl(
        self,
        pnl: float,
        starting_balance: Optional[float] = None,
        ending_balance: Optional[float] = None,
    ) -> None:
        """Upsert today's P&L record."""
        today = utc_now().strftime("%Y-%m-%d")
        trades_count = await self.get_today_trade_count()
        signals_count = await self.get_today_signals_count()

        await self.conn.execute(
            """
            INSERT INTO daily_stats
                (trade_date, starting_balance, ending_balance, total_pnl,
                 trades_opened, signals_received)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                ending_balance = excluded.ending_balance,
                total_pnl = excluded.total_pnl,
                trades_opened = excluded.trades_opened,
                signals_received = excluded.signals_received
            """,
            (today, starting_balance, ending_balance, pnl, trades_count, signals_count),
        )
        await self.conn.commit()

    async def get_recent_signals(self, limit: int = 20) -> list[dict]:
        """Get the most recent signal records."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM signals
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def cleanup_non_signal_telegram_messages(self, max_age_hours: int = 24) -> int:
        """
        Delete old telegram message events that were not parsed as trading signals.

        A message is considered a signal when `signals.parsed_action` is present.
        """
        cutoff = (utc_now() - timedelta(hours=max_age_hours)).isoformat()

        cursor = await self.conn.execute(
            """
            DELETE FROM telegram_messages
            WHERE created_at < ?
              AND message_id NOT IN (
                  SELECT DISTINCT message_id
                  FROM signals
                  WHERE message_id IS NOT NULL
                    AND COALESCE(parsed_action, '') != ''
              )
            """,
            (cutoff,),
        )
        deleted = cursor.rowcount
        await self.conn.commit()

        if deleted > 0:
            logger.info(
                "Cleaned up %d non-signal telegram events older than %dh",
                deleted,
                max_age_hours,
            )
        return deleted

    async def deduplicate_telegram_events(self) -> int:
        """Remove duplicate telegram event rows, keeping the earliest row per unique payload."""
        cursor = await self.conn.execute(
            """
            DELETE FROM telegram_messages
            WHERE id IN (
                SELECT t1.id
                FROM telegram_messages t1
                JOIN telegram_messages t2
                  ON t1.message_id = t2.message_id
                 AND t1.event_type = t2.event_type
                 AND COALESCE(t1.channel_id, '') = COALESCE(t2.channel_id, '')
                 AND COALESCE(t1.text_before, '') = COALESCE(t2.text_before, '')
                 AND COALESCE(t1.text_after, '') = COALESCE(t2.text_after, '')
                 AND t1.id > t2.id
            )
            """
        )
        removed = cursor.rowcount
        await self.conn.commit()
        if removed > 0:
            logger.info("Removed %d duplicate telegram event rows", removed)
        return removed

    async def delete_all_data(self) -> dict[str, int]:
        """Delete all data from runtime tables (keeps schema)."""
        results: dict[str, int] = {}
        for table in ("signals", "trades", "telegram_messages", "daily_stats"):
            cursor = await self.conn.execute(f"DELETE FROM {table}")
            results[table] = cursor.rowcount
        await self.conn.commit()
        return results

    async def delete_rows_by_ids(self, table: str, ids: list[int]) -> int:
        """Delete selected rows by primary key id from a safe table allowlist."""
        allowed = {"signals", "trades", "telegram_messages", "daily_stats"}
        if table not in allowed:
            raise ValueError(f"Unsupported table: {table}")
        if not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)
        cursor = await self.conn.execute(
            f"DELETE FROM {table} WHERE id IN ({placeholders})",
            tuple(ids),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def delete_by_message_ids(self, message_ids: list[int]) -> dict[str, int]:
        """Delete telegram/signal rows for selected Telegram message IDs."""
        if not message_ids:
            return {"telegram_messages": 0, "signals": 0}

        placeholders = ",".join("?" for _ in message_ids)

        cur_tg = await self.conn.execute(
            f"DELETE FROM telegram_messages WHERE message_id IN ({placeholders})",
            tuple(message_ids),
        )
        cur_sig = await self.conn.execute(
            f"DELETE FROM signals WHERE message_id IN ({placeholders})",
            tuple(message_ids),
        )
        await self.conn.commit()
        return {
            "telegram_messages": cur_tg.rowcount,
            "signals": cur_sig.rowcount,
        }

    # ── Cleanup ─────────────────────────────────────────────────────────

    async def cleanup_old_records(self, days: int = 90) -> int:
        """Delete signal and trade records older than N days."""
        cutoff = (utc_now() - timedelta(days=days)).isoformat()

        cursor = await self.conn.execute(
            "DELETE FROM signals WHERE created_at < ?", (cutoff,)
        )
        signals_deleted = cursor.rowcount

        cursor = await self.conn.execute(
            "DELETE FROM trades WHERE created_at < ?", (cutoff,)
        )
        trades_deleted = cursor.rowcount

        await self.conn.commit()
        total = signals_deleted + trades_deleted
        if total > 0:
            logger.info(
                "Cleaned up %d old records (%d signals, %d trades)",
                total, signals_deleted, trades_deleted,
            )
        return total
