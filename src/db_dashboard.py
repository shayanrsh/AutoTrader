"""
AutoTrader - Database Dashboard (curses)

Interactive terminal dashboard for browsing the SQLite database without Textual.
Features:
- Overview metrics
- Latest messages
- Search messages
- Edited / Deleted message views
- Trades view
- Custom SQL query view
"""

from __future__ import annotations

import curses
import json
import sqlite3
import re
from pathlib import Path
from typing import Callable, Optional

_BOOTSTRAP_SQL = """
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

CREATE TABLE IF NOT EXISTS telegram_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL,
    channel_id      TEXT,
    event_type      TEXT NOT NULL,
    text_before     TEXT,
    text_after      TEXT,
    parse_status    TEXT    NOT NULL DEFAULT 'PENDING',
    parsed_at       TEXT,
    parser_source   TEXT,
    parse_error     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

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
"""


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _resolve_db_path() -> Path:
    env = _read_env_values(_project_root() / "config.env")
    raw = env.get("DATABASE_PATH", "data/autotrader.db").strip()
    db_path = Path(raw)
    if not db_path.is_absolute():
        db_path = _project_root() / db_path
    return db_path


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return row is not None


def _truncate(value: object, width: int) -> str:
    text = "" if value is None else str(value)
    if width <= 1:
        return text[:width]
    if len(text) <= width:
        return text
    return text[: width - 1] + "~"


class Dashboard:
    def __init__(self, stdscr: curses.window, db_path: Path) -> None:
        self.stdscr = stdscr
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row

        self.menu_items: list[tuple[str, Callable[[], None]]] = [
            ("Overview", self.show_overview),
            ("All Channel Messages", self.show_all_channel_messages),
            ("Latest Signals", self.show_latest_signals),
            ("Search Messages", self.search_messages),
            ("Edited Messages", self.show_edited_messages),
            ("Deleted Messages", self.show_deleted_messages),
            ("Trades", self.show_trades),
            ("Delete Data", self.delete_data_menu),
            ("Run SQL Query", self.run_sql_query),
            ("Refresh", self.refresh_current_view),
            ("Quit", self.quit),
        ]
        self.selected_menu = 0
        self.running = True

        self.title = "Overview"
        self.columns: list[str] = []
        self.rows: list[sqlite3.Row | dict[str, object]] = []
        self.offset = 0
        self.selected_row = 0
        self.message = ""
        self.current_view_action: Callable[[], None] = self.show_overview
        self._colors_enabled = False

    def close(self) -> None:
        self.conn.close()

    def quit(self) -> None:
        self.running = False

    def refresh(self) -> None:
        # Reopen connection so dashboard sees external writes quickly.
        self.conn.close()
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.message = "Refreshed from disk"

    def refresh_current_view(self) -> None:
        self.refresh()
        self.current_view_action()

    def _parse_int_csv(self, raw: str) -> list[int]:
        ids: list[int] = []
        for part in raw.split(","):
            token = part.strip()
            if not token:
                continue
            ids.append(int(token))
        return ids

    def _deduplicate_telegram_events(self) -> int:
        cursor = self.conn.execute(
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
        self.conn.commit()
        return cursor.rowcount

    def _delete_all_data(self) -> dict[str, int]:
        results: dict[str, int] = {}
        for table in ("signals", "trades", "telegram_messages", "daily_stats"):
            cursor = self.conn.execute(f"DELETE FROM {table}")
            results[table] = cursor.rowcount
        self.conn.commit()
        return results

    def _delete_rows_by_ids(self, table: str, ids: list[int]) -> int:
        allowed = {"signals", "trades", "telegram_messages", "daily_stats"}
        if table not in allowed:
            raise ValueError(f"Unsupported table: {table}")
        if not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)
        cursor = self.conn.execute(
            f"DELETE FROM {table} WHERE id IN ({placeholders})",
            tuple(ids),
        )
        self.conn.commit()
        return cursor.rowcount

    def _delete_by_message_ids(self, message_ids: list[int]) -> dict[str, int]:
        if not message_ids:
            return {"telegram_messages": 0, "signals": 0}

        placeholders = ",".join("?" for _ in message_ids)
        cur_tg = self.conn.execute(
            f"DELETE FROM telegram_messages WHERE message_id IN ({placeholders})",
            tuple(message_ids),
        )
        cur_sig = self.conn.execute(
            f"DELETE FROM signals WHERE message_id IN ({placeholders})",
            tuple(message_ids),
        )
        self.conn.commit()
        return {
            "telegram_messages": cur_tg.rowcount,
            "signals": cur_sig.rowcount,
        }

    def delete_data_menu(self) -> None:
        self.title = "Delete Data"
        self.columns = ["option", "meaning"]
        self.rows = [
            {"option": "1", "meaning": "Delete ALL data tables (signals, trades, telegram_messages, daily_stats)"},
            {"option": "2", "meaning": "Delete selected row id(s) from one table"},
            {"option": "3", "meaning": "Delete by Telegram message_id(s) from telegram_messages + signals"},
            {"option": "4", "meaning": "Cancel and return"},
        ]
        self.offset = 0
        self.message = "Read options, then press any key to continue to prompt"
        self.draw()
        self.stdscr.getch()

        choice = self._prompt("Delete option (1/2/3/4): ")
        if choice == "4" or not choice:
            self.message = "Delete canceled"
            return

        status_message = ""
        try:
            if choice == "1":
                confirm = self._prompt("Type DELETE ALL to confirm: ")
                if confirm != "DELETE ALL":
                    self.message = "Confirmation failed, nothing deleted"
                    return
                result = self._delete_all_data()
                status_message = f"Deleted all data: {result}"

            elif choice == "2":
                table = self._prompt("Table (signals/trades/telegram_messages/daily_stats): ").strip()
                ids_raw = self._prompt("Row id(s), comma-separated (e.g. 1,2,9): ")
                ids = self._parse_int_csv(ids_raw)
                deleted = self._delete_rows_by_ids(table, ids)
                status_message = f"Deleted {deleted} row(s) from {table}"

            elif choice == "3":
                ids_raw = self._prompt("Telegram message_id(s), comma-separated: ")
                message_ids = self._parse_int_csv(ids_raw)
                deleted_map = self._delete_by_message_ids(message_ids)
                status_message = (
                    "Deleted by message_id: "
                    f"telegram_messages={deleted_map['telegram_messages']}, "
                    f"signals={deleted_map['signals']}"
                )
            else:
                status_message = "Unknown option"
        except Exception as exc:
            status_message = f"Delete failed: {exc}"

        self.refresh_current_view()
        self.message = status_message

    def _fetch(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def _row_value(self, row: sqlite3.Row | dict[str, object], col: str) -> object:
        if isinstance(row, sqlite3.Row):
            return row[col] if col in row.keys() else ""
        if isinstance(row, dict):
            return row.get(col, "")
        return ""

    def _selected_row_data(self) -> Optional[sqlite3.Row | dict[str, object]]:
        if not self.rows:
            return None
        idx = max(0, min(self.selected_row, len(self.rows) - 1))
        self.selected_row = idx
        return self.rows[idx]

    def _safe_json_obj_from_row(self, row: sqlite3.Row | dict[str, object]) -> Optional[object]:
        raw = self._row_value(row, "parsed_json")
        if raw:
            try:
                return json.loads(str(raw))
            except Exception:
                pass

        if self.title == "Latest Signals":
            return {
                "id": self._row_value(row, "id"),
                "message_id": self._row_value(row, "message_id"),
                "trade_status": self._row_value(row, "trade_status"),
                "action": self._row_value(row, "parsed_action"),
                "entry_price": self._row_value(row, "parsed_entry"),
                "stop_loss": self._row_value(row, "parsed_sl"),
                "take_profits": self._row_value(row, "parsed_tp1"),
                "parser_source": self._row_value(row, "parser_source"),
                "raw_text": self._row_value(row, "raw_text"),
                "created_at": self._row_value(row, "created_at"),
            }
        return None

    def _line_tokens(self, line: str) -> list[tuple[str, int]]:
        if not self._colors_enabled:
            return [(line, curses.A_NORMAL)]

        tokens: list[tuple[str, int]] = []
        pattern = re.compile(r'"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b|[{}\[\],:]')
        last = 0

        for match in pattern.finditer(line):
            start, end = match.span()
            if start > last:
                tokens.append((line[last:start], curses.A_NORMAL))

            token = match.group(0)
            attr = curses.A_NORMAL
            if token in "{}[],:":
                attr = curses.color_pair(1)
            elif token.startswith('"') and token.endswith('"'):
                if end < len(line) and line[end:end + 1] == ":":
                    attr = curses.color_pair(2) | curses.A_BOLD
                elif end < len(line) and line[end:].lstrip().startswith(":"):
                    attr = curses.color_pair(2) | curses.A_BOLD
                else:
                    attr = curses.color_pair(3)
            elif token in ("true", "false", "null"):
                attr = curses.color_pair(5)
            else:
                attr = curses.color_pair(4)

            tokens.append((token, attr))
            last = end

        if last < len(line):
            tokens.append((line[last:], curses.A_NORMAL))

        return tokens

    def _render_colored_line(self, y: int, x: int, line: str, max_width: int) -> None:
        if max_width <= 0:
            return

        used = 0
        for token, attr in self._line_tokens(line):
            if used >= max_width:
                break
            part = token[: max_width - used]
            try:
                self.stdscr.addnstr(y, x + used, part, max_width - used, attr)
            except curses.error:
                pass
            used += len(part)

    def _show_json_modal(self, title: str, obj: object) -> None:
        formatted = json.dumps(obj, ensure_ascii=False, indent=2)
        lines = formatted.splitlines() or ["{}"]
        scroll = 0

        while True:
            self.stdscr.erase()
            h, w = self.stdscr.getmaxyx()

            header = f"{title} | colorful JSON view"
            self.stdscr.addnstr(0, 0, _truncate(header, w - 1), w - 1, curses.A_REVERSE)

            visible = max(1, h - 3)
            max_scroll = max(0, len(lines) - visible)
            scroll = max(0, min(scroll, max_scroll))

            for i in range(visible):
                idx = scroll + i
                if idx >= len(lines):
                    break
                self._render_colored_line(1 + i, 0, lines[idx], w - 1)

            footer = "Keys: j/k scroll | PgDn/PgUp page | Home/End | Enter/q/ESC back"
            self.stdscr.addnstr(h - 1, 0, _truncate(footer, w - 1), w - 1, curses.A_REVERSE)
            self.stdscr.refresh()

            ch = self.stdscr.getch()
            if ch in (ord("q"), 27, 10, 13, curses.KEY_ENTER):
                break
            if ch in (ord("j"), curses.KEY_DOWN):
                scroll = min(max_scroll, scroll + 1)
            elif ch in (ord("k"), curses.KEY_UP):
                scroll = max(0, scroll - 1)
            elif ch == curses.KEY_NPAGE:
                scroll = min(max_scroll, scroll + max(1, visible - 2))
            elif ch == curses.KEY_PPAGE:
                scroll = max(0, scroll - max(1, visible - 2))
            elif ch == curses.KEY_HOME:
                scroll = 0
            elif ch == curses.KEY_END:
                scroll = max_scroll

    def view_selected_signal_json(self) -> None:
        row = self._selected_row_data()
        if row is None:
            self.message = "No row selected"
            return

        obj = self._safe_json_obj_from_row(row)
        if obj is None:
            self.message = "Selected row has no JSON signal data"
            return

        signal_id = self._row_value(row, "id")
        self._show_json_modal(f"Signal #{signal_id}", obj)
        self.message = f"Viewed signal JSON for row id={signal_id}"

    def _prompt(self, label: str) -> str:
        height, _ = self.stdscr.getmaxyx()
        curses.echo()
        curses.curs_set(1)
        self.stdscr.move(height - 2, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addstr(height - 2, 0, label)
        self.stdscr.refresh()
        raw = self.stdscr.getstr(height - 2, len(label), 512)
        curses.noecho()
        curses.curs_set(0)
        return raw.decode("utf-8", errors="ignore").strip()

    def show_overview(self) -> None:
        self.title = "Overview"
        signals_count = 0
        if _table_exists(self.conn, "signals"):
            signals_count = self.conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM signals
                WHERE COALESCE(parsed_action, '') != ''
                """
            ).fetchone()["c"]
        trades_count = self.conn.execute("SELECT COUNT(*) AS c FROM trades").fetchone()["c"] if _table_exists(self.conn, "trades") else 0

        latest_signal = None
        if _table_exists(self.conn, "signals"):
            latest_signal = self.conn.execute(
                """
                SELECT message_id, trade_status, created_at
                FROM signals
                WHERE COALESCE(parsed_action, '') != ''
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        edited_count = 0
        deleted_count = 0
        total_channel_messages = 0
        if _table_exists(self.conn, "telegram_messages"):
            total_channel_messages = self.conn.execute(
                "SELECT COUNT(*) AS c FROM telegram_messages WHERE event_type='new'"
            ).fetchone()["c"]
            edited_count = self.conn.execute(
                "SELECT COUNT(*) AS c FROM telegram_messages WHERE event_type='edit'"
            ).fetchone()["c"]
            deleted_count = self.conn.execute(
                "SELECT COUNT(*) AS c FROM telegram_messages WHERE event_type='delete'"
            ).fetchone()["c"]

        overview = [
            {"metric": "Database file", "value": str(self.db_path)},
            {"metric": "Signals", "value": signals_count},
            {"metric": "Trades", "value": trades_count},
            {"metric": "All channel messages", "value": total_channel_messages},
            {"metric": "Edited messages", "value": edited_count},
            {"metric": "Deleted messages", "value": deleted_count},
        ]

        if latest_signal is not None:
            overview.append(
                {
                    "metric": "Latest signal",
                    "value": f"message_id={latest_signal['message_id']} status={latest_signal['trade_status']} at {latest_signal['created_at']}",
                }
            )

        self.columns = ["metric", "value"]
        self.rows = overview
        self.offset = 0
        self.message = "Use arrow keys to navigate rows. Press / in Search Messages menu for text search prompt."

    def show_all_channel_messages(self) -> None:
        self.title = "All Channel Messages"
        if _table_exists(self.conn, "telegram_messages"):
            self.columns = [
                "id",
                "message_id",
                "channel_id",
                "parse_status",
                "text_after",
                "created_at",
            ]
            self.rows = self._fetch(
                """
                SELECT id, message_id, channel_id,
                       COALESCE(parse_status, 'PENDING') AS parse_status,
                       COALESCE(text_after, text_before, '') AS text_after,
                       created_at
                FROM telegram_messages
                WHERE event_type = 'new'
                ORDER BY id DESC
                LIMIT 300
                """
            )
            self.offset = 0
            self.message = "Showing all channel messages from telegram_messages"
            return

        self.columns = ["info"]
        self.rows = [{"info": "No telegram_messages table found yet."}]
        self.offset = 0
        self.message = "Run telegram_listener with --persist-db (or default) to store messages"

    def show_latest_signals(self) -> None:
        self.title = "Latest Signals"
        if _table_exists(self.conn, "signals"):
            self.columns = [
                "id",
                "message_id",
                "trade_status",
                "parsed_json",
                "raw_text",
                "created_at",
            ]
            base_rows = self._fetch(
                """
                SELECT id, message_id, trade_status,
                       parsed_action, parsed_entry, parsed_sl, parsed_tp1,
                       parser_source, raw_text, created_at
                FROM signals
                WHERE COALESCE(parsed_action, '') != ''
                ORDER BY id DESC
                LIMIT 300
                """
            )

            self.rows = []
            for row in base_rows:
                parsed_obj = {
                    "action": row["parsed_action"],
                    "entry_price": row["parsed_entry"],
                    "stop_loss": row["parsed_sl"],
                    "take_profits": row["parsed_tp1"],
                    "parser_source": row["parser_source"],
                }
                self.rows.append(
                    {
                        "id": row["id"],
                        "message_id": row["message_id"],
                        "trade_status": row["trade_status"],
                        "parsed_json": json.dumps(parsed_obj, ensure_ascii=False),
                        "raw_text": row["raw_text"],
                        "created_at": row["created_at"],
                    }
                )
        else:
            self.columns = ["info"]
            self.rows = [{"info": "No signals table found."}]
        self.offset = 0
        self.message = "Showing valid parsed signals only"

    def _show_event_type(self, event_type: str, title: str) -> None:
        self.title = title
        if not _table_exists(self.conn, "telegram_messages"):
            self.columns = ["info"]
            self.rows = [
                {"info": "No telegram_messages table yet. Update listeners to persist edit/delete events."}
            ]
            self.offset = 0
            self.message = "No event table"
            return

        self.columns = ["id", "message_id", "channel_id", "text_before", "text_after", "created_at"]
        if event_type == "delete":
            self.rows = self._fetch(
                """
                SELECT t.id,
                       t.message_id,
                       t.channel_id,
                       COALESCE(
                           t.text_before,
                           (
                               SELECT COALESCE(p.text_after, p.text_before)
                               FROM telegram_messages p
                               WHERE p.message_id = t.message_id
                                 AND p.id < t.id
                               ORDER BY p.id DESC
                               LIMIT 1
                           ),
                           ''
                       ) AS text_before,
                       COALESCE(t.text_after, '[deleted]') AS text_after,
                       t.created_at
                FROM telegram_messages t
                WHERE t.event_type = 'delete'
                ORDER BY t.id DESC
                LIMIT 300
                """
            )
        else:
            self.rows = self._fetch(
                """
                SELECT id,
                       message_id,
                       channel_id,
                       COALESCE(text_before, '') AS text_before,
                       COALESCE(text_after, '') AS text_after,
                       created_at
                FROM telegram_messages
                WHERE event_type = ?
                ORDER BY id DESC
                LIMIT 300
                """,
                (event_type,),
            )
        self.offset = 0
        self.message = f"Filtered by event_type={event_type}"

    def show_edited_messages(self) -> None:
        self._show_event_type("edit", "Edited Messages")

    def show_deleted_messages(self) -> None:
        self._show_event_type("delete", "Deleted Messages")

    def show_trades(self) -> None:
        self.title = "Trades"
        if not _table_exists(self.conn, "trades"):
            self.columns = ["info"]
            self.rows = [{"info": "No trades table found."}]
            self.offset = 0
            self.message = "No trades table"
            return

        self.columns = [
            "id",
            "order_ticket",
            "symbol",
            "action",
            "volume",
            "status",
            "price",
            "created_at",
        ]
        self.rows = self._fetch(
            """
            SELECT id, order_ticket, symbol, action, volume, status, price, created_at
            FROM trades
            ORDER BY id DESC
            LIMIT 300
            """
        )
        self.offset = 0
        self.message = "Latest trades"

    def search_messages(self) -> None:
        self.title = "Search Messages"
        query = self._prompt("Search text (message id, text, status): ")
        if not query:
            self.message = "Search canceled"
            return

        like = f"%{query}%"
        if _table_exists(self.conn, "telegram_messages"):
            self.columns = ["id", "message_id", "channel_id", "event_type", "text_after", "created_at"]
            self.rows = self._fetch(
                """
                SELECT id, message_id, channel_id, event_type,
                       COALESCE(text_after, text_before, '') AS text_after,
                       created_at
                FROM telegram_messages
                WHERE CAST(message_id AS TEXT) LIKE ?
                   OR COALESCE(text_after, text_before, '') LIKE ?
                   OR event_type LIKE ?
                ORDER BY id DESC
                LIMIT 500
                """,
                (like, like, like),
            )
        else:
            if _table_exists(self.conn, "signals"):
                self.columns = ["id", "message_id", "trade_status", "raw_text", "created_at"]
                self.rows = self._fetch(
                    """
                    SELECT id, message_id, trade_status, raw_text, created_at
                    FROM signals
                    WHERE CAST(message_id AS TEXT) LIKE ?
                       OR raw_text LIKE ?
                       OR COALESCE(trade_status, '') LIKE ?
                    ORDER BY id DESC
                    LIMIT 500
                    """,
                    (like, like, like),
                )
            else:
                self.columns = ["info"]
                self.rows = [{"info": "No message tables available to search."}]

        self.offset = 0
        self.message = f"Search: '{query}' ({len(self.rows)} rows)"

    def run_sql_query(self) -> None:
        self.title = "Run SQL Query"
        query = self._prompt("SQL (SELECT only): ")
        if not query:
            self.message = "SQL canceled"
            return

        if not query.strip().lower().startswith("select"):
            self.message = "Only SELECT statements are allowed"
            return

        try:
            cur = self.conn.execute(query)
            result = cur.fetchall()
            self.columns = [d[0] for d in cur.description] if cur.description else []
            self.rows = result
            self.offset = 0
            self.message = f"Query OK ({len(result)} rows)"
        except Exception as exc:
            self.columns = ["error"]
            self.rows = [{"error": str(exc)}]
            self.offset = 0
            self.message = "SQL error"

    def draw(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        if not self.rows:
            self.offset = 0
            self.selected_row = 0
        else:
            self.selected_row = max(0, min(self.selected_row, len(self.rows) - 1))

        header = f"AutoTrader DB Dashboard | {self.title}"
        self.stdscr.addnstr(0, 0, header, w - 1, curses.A_REVERSE)

        menu_y = 2
        self.stdscr.addnstr(menu_y - 1, 0, "Menu", w - 1, curses.A_BOLD)
        for i, (label, _) in enumerate(self.menu_items):
            attr = curses.A_STANDOUT if i == self.selected_menu else curses.A_NORMAL
            self.stdscr.addnstr(menu_y + i, 0, f"{i + 1}. {label}", max(20, w // 4), attr)

        content_x = max(24, w // 4 + 2)
        content_w = max(10, w - content_x - 1)

        if self.columns:
            col_line = " | ".join(self.columns)
            self.stdscr.addnstr(2, content_x, _truncate(col_line, content_w), content_w, curses.A_BOLD)

        visible_rows = max(5, h - 6)

        if self.rows:
            if self.selected_row < self.offset:
                self.offset = self.selected_row
            elif self.selected_row >= self.offset + visible_rows:
                self.offset = self.selected_row - visible_rows + 1

        data_slice = self.rows[self.offset : self.offset + visible_rows]
        for idx, row in enumerate(data_slice):
            y = 3 + idx
            if y >= h - 2:
                break
            if isinstance(row, sqlite3.Row):
                values = [row[col] if col in row.keys() else "" for col in self.columns]
            elif isinstance(row, dict):
                values = [row.get(col, "") for col in self.columns]
            else:
                values = [str(row)]
            line = " | ".join(_truncate(v, 30) for v in values)
            row_index = self.offset + idx
            row_attr = curses.A_STANDOUT if row_index == self.selected_row else curses.A_NORMAL
            self.stdscr.addnstr(y, content_x, _truncate(line, content_w), content_w, row_attr)

        footer = "Keys: Up/Down menu | Enter open | j/k select row | v view JSON | PgDn/PgUp page | r refresh | q quit"
        self.stdscr.addnstr(h - 1, 0, _truncate(footer, w - 1), w - 1, curses.A_REVERSE)
        if self.message:
            self.stdscr.addnstr(h - 2, 0, _truncate(self.message, w - 1), w - 1)

        self.stdscr.refresh()

    def run(self) -> None:
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)    # punctuation
            curses.init_pair(2, curses.COLOR_YELLOW, -1)  # keys
            curses.init_pair(3, curses.COLOR_GREEN, -1)   # string values
            curses.init_pair(4, curses.COLOR_MAGENTA, -1) # numbers
            curses.init_pair(5, curses.COLOR_BLUE, -1)    # booleans/null
            self._colors_enabled = True

        self.show_overview()

        while self.running:
            self.draw()
            ch = self.stdscr.getch()

            if ch in (ord("q"), 27):
                self.running = False
            elif ch == ord("r"):
                self.refresh_current_view()
            elif ch in (curses.KEY_UP, ord("K")):
                self.selected_menu = max(0, self.selected_menu - 1)
            elif ch in (curses.KEY_DOWN, ord("J")):
                self.selected_menu = min(len(self.menu_items) - 1, self.selected_menu + 1)
            elif ch in (10, 13, curses.KEY_ENTER):
                label, action = self.menu_items[self.selected_menu]
                # Keep modal actions (like Delete Data) from becoming the active refresh view.
                if label not in {"Refresh", "Quit", "Delete Data"}:
                    self.current_view_action = action
                action()
            elif ch in (ord("j"),):
                if self.rows:
                    self.selected_row = min(len(self.rows) - 1, self.selected_row + 1)
            elif ch in (ord("k"),):
                if self.rows:
                    self.selected_row = max(0, self.selected_row - 1)
            elif ch == curses.KEY_NPAGE:
                if self.rows:
                    h, _ = self.stdscr.getmaxyx()
                    page = max(1, h - 6)
                    self.selected_row = min(len(self.rows) - 1, self.selected_row + page)
            elif ch == curses.KEY_PPAGE:
                if self.rows:
                    h, _ = self.stdscr.getmaxyx()
                    page = max(1, h - 6)
                    self.selected_row = max(0, self.selected_row - page)
            elif ch == ord("v"):
                self.view_selected_signal_json()


def _run_curses(stdscr: curses.window, db_path: Path) -> None:
    dashboard = Dashboard(stdscr, db_path)
    try:
        dashboard.run()
    finally:
        dashboard.close()


def main() -> None:
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_BOOTSTRAP_SQL)
        conn.commit()

    # One-time dedupe pass so historical duplicates from old runs are cleaned.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
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
        conn.commit()

    curses.wrapper(lambda stdscr: _run_curses(stdscr, db_path))


if __name__ == "__main__":
    main()
