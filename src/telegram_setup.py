"""
AutoTrader — Telegram Setup Utilities

Interactive Telegram helper with two main modes:
1) setup: authenticate/create session, list channels, optionally update TELEGRAM_CHANNEL_ID
2) search: reuse existing session to search/list channels without full login every time

Also contains shared helpers used by the runtime listener.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import re
from pathlib import Path
from typing import Callable, Union

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    PasswordHashInvalidError,
    SessionPasswordNeededError,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "AutoTrader Telegram utility. Use 'setup' for first login + channel selection, "
            "or 'search' to find channels later using existing session."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["setup", "search"],
        default=None,
        help="setup: login + optional TELEGRAM_CHANNEL_ID write, search: list/search channels",
    )
    parser.add_argument(
        "--query",
        default="",
        help="Optional filter used with --mode search (matches title, username, id)",
    )
    return parser.parse_args()


def _read_env_values(env_path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE reader for config.env without external dependencies."""
    result: dict[str, str] = {}
    if not env_path.exists():
        return result

    def _parse_env_value(raw_value: str) -> str:
        value = raw_value.strip()
        if not value:
            return ""

        if value[0] in {'"', "'"}:
            quote = value[0]
            if len(value) >= 2 and value[-1] == quote:
                value = value[1:-1]
            else:
                value = value[1:]

            if quote == '"':
                value = value.replace(r"\\", "\\").replace(r'\"', '"')
            else:
                value = value.replace(r"\\", "\\").replace(r"\'", "'")
            return value

        # Treat inline comments only for unquoted values.
        return re.sub(r"\s+#.*$", "", value).strip()

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        result[key.strip()] = _parse_env_value(value)

    return result


def normalize_channel_token(token: str) -> str:
    """Normalize one channel selector token from config/user input."""
    value = token.strip()
    if not value:
        raise ValueError("empty channel token")
    if value.startswith("@"):
        value = value[1:]
    return value


def parse_channel_ids(raw: str) -> list[str]:
    """
    Parse TELEGRAM_CHANNEL_ID value which may contain comma-separated channels.

    Supported forms per token:
    - Numeric channel id: -1001234567890
    - Username: some_channel
    """
    tokens = [normalize_channel_token(p) for p in str(raw).split(",") if p.strip()]
    if not tokens:
        return []

    normalized: list[str] = []
    for token in tokens:
        if token.lstrip("-").isdigit():
            normalized.append(token)
            continue

        if not token.replace("_", "").isalnum():
            raise ValueError(
                f"Invalid channel token '{token}'. Use numeric id or username."
            )
        normalized.append(token)
    return normalized


def channel_tokens_to_targets(channel_tokens: list[str]) -> list[Union[int, str]]:
    """Convert parsed channel tokens to Telethon-friendly targets."""
    targets: list[Union[int, str]] = []
    for token in channel_tokens:
        if token.lstrip("-").isdigit():
            targets.append(int(token))
        else:
            targets.append(token)
    return targets


def create_telegram_client(
    api_id: int,
    api_hash: str,
    session_path: str,
) -> TelegramClient:
    """Create a Telethon client with retry/reconnect defaults."""
    resolved_session = Path(session_path)
    if not resolved_session.is_absolute():
        resolved_session = _project_root() / resolved_session
    resolved_session.parent.mkdir(parents=True, exist_ok=True)

    return TelegramClient(
        str(resolved_session),
        api_id,
        api_hash,
        auto_reconnect=True,
        retry_delay=5,
        connection_retries=10,
        request_retries=5,
    )


def _update_env_key(env_path: Path, key: str, value: str) -> None:
    """Update or append KEY=value in a dotenv file while preserving other lines."""
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    write_value = value
    if key == "TELEGRAM_PASSWORD":
        escaped = value.replace("\\", r"\\").replace('"', r'\"')
        write_value = f'"{escaped}"'

    new_line = f"{key}={write_value}"
    replaced = False
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            out.append(new_line)
            replaced = True
        else:
            out.append(line)

    if not replaced:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(new_line)

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _resolve_session_path(raw_path: str) -> Path:
    session = Path(raw_path)
    if not session.is_absolute():
        session = _project_root() / session
    session.parent.mkdir(parents=True, exist_ok=True)
    return session


def _sanitize_csv_cell(value: str) -> str:
    return str(value).replace("\n", " ").replace("\r", " ").replace(",", " ").strip()


def _save_channel_inventory(channel_rows: list[tuple[int, str, str, str]]) -> Path:
    """Save discovered channels to data/telegram_channels.csv for later lookup."""
    out_path = _project_root() / "data" / "telegram_channels.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["id,title,access,token"]
    for entity_id, title, access_value, token in channel_rows:
        lines.append(
            ",".join(
                [
                    _sanitize_csv_cell(str(entity_id)),
                    _sanitize_csv_cell(title),
                    _sanitize_csv_cell(access_value),
                    _sanitize_csv_cell(token),
                ]
            )
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def _merge_channel_tokens(existing: list[str], incoming: list[str]) -> list[str]:
    """Merge channel tokens while preserving order and removing duplicates."""
    seen: set[str] = set()
    merged: list[str] = []

    for token in existing + incoming:
        if token not in seen:
            seen.add(token)
            merged.append(token)
    return merged


def _append_channels_to_env(env_path: Path, new_tokens: list[str]) -> str:
    """Append new channel tokens to TELEGRAM_CHANNEL_ID in config.env."""
    env_values = _read_env_values(env_path)
    current_raw = (env_values.get("TELEGRAM_CHANNEL_ID") or "").strip()

    current_tokens: list[str] = []
    if current_raw:
        try:
            current_tokens = parse_channel_ids(current_raw)
        except ValueError:
            # Keep it resilient even if current env value is malformed.
            current_tokens = [t.strip() for t in current_raw.split(",") if t.strip()]

    merged_tokens = _merge_channel_tokens(current_tokens, new_tokens)
    merged_value = ",".join(merged_tokens)
    _update_env_key(env_path, "TELEGRAM_CHANNEL_ID", merged_value)
    return merged_value


def _filter_channel_rows(
    channel_rows: list[tuple[int, str, str, str]], query: str
) -> list[tuple[int, str, str, str]]:
    if not query.strip():
        return channel_rows

    needle = query.strip().lower()
    filtered: list[tuple[int, str, str, str]] = []
    for row in channel_rows:
        entity_id, title, access_value, token = row
        haystack = f"{entity_id} {title} {access_value} {token}".lower()
        if needle in haystack:
            filtered.append(row)
    return filtered


def _parse_channel_selection_input(
    raw: str,
    channel_rows: list[tuple[int, str, str, str]],
) -> list[str]:
    """
    Parse user channel selection.

    Supports comma-separated values containing:
    - listed row numbers: 194. or 194
    - channel ids/usernames: -100123..., my_channel
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return []

    tokens: list[str] = []
    max_index = len(channel_rows)

    for part in parts:
        # Numbered-row selection, e.g. "194." or "194"
        index_match = re.fullmatch(r"(\d+)\.??", part)
        if index_match:
            idx = int(index_match.group(1))
            if 1 <= idx <= max_index:
                # token is raw channel id string from discovered rows
                tokens.append(channel_rows[idx - 1][3])
                continue

        # Fallback to normal channel token parsing (id or username)
        parsed = parse_channel_ids(part)
        if not parsed:
            raise ValueError(f"Invalid channel selector '{part}'")
        tokens.extend(parsed)

    return tokens


def _print_channel_rows(channel_rows: list[tuple[int, str, str, str]]) -> None:
    if not channel_rows:
        print("No channels/groups found.")
        return

    print("\nAvailable channels/groups:")
    for idx, row in enumerate(channel_rows, start=1):
        entity_id, title, access_value, raw_id = row
        print(f"{idx:>3}. {title} | id={entity_id} | {access_value} | token={raw_id}")


def _print_filtered_channel_rows(
    channel_rows: list[tuple[int, str, str, str]],
    query: str,
) -> None:
    """Print filtered channels while preserving original global list numbering."""
    needle = query.strip().lower()
    if not needle:
        _print_channel_rows(channel_rows)
        return

    print(f"\nSearch query: {query}")
    print("Matched channels/groups:")
    found = False

    for idx, row in enumerate(channel_rows, start=1):
        entity_id, title, access_value, raw_id = row
        haystack = f"{entity_id} {title} {access_value} {raw_id}".lower()
        if needle in haystack:
            found = True
            print(f"{idx:>3}. {title} | id={entity_id} | {access_value} | token={raw_id}")

    if not found:
        print("No channels/groups found.")


async def _ensure_authorized(
    client: TelegramClient,
    phone: str,
    password_from_env: str,
    env_path: Path,
) -> None:
    """Connect and ensure the user is authorized, using saved session when possible."""
    await client.connect()

    if await client.is_user_authorized():
        print("[OK] Existing Telegram session found. Login step skipped.")
        return

    print("No active session found. Starting interactive login...")

    async def _complete_2fa_login(initial_password: str = "") -> None:
        """Finalize login with 2FA password without restarting code delivery."""
        password_candidate = initial_password.strip()
        used_saved_password = bool(password_candidate)

        while True:
            if not password_candidate:
                password_candidate = getpass.getpass("Telegram 2FA password: ").strip()
                if not password_candidate:
                    raise RuntimeError("Telegram 2FA password cannot be empty")

            try:
                await client.sign_in(password=password_candidate)
                _update_env_key(env_path, "TELEGRAM_PASSWORD", password_candidate)
                print("Updated TELEGRAM_PASSWORD in config.env")
                return
            except PasswordHashInvalidError:
                if used_saved_password:
                    print("Saved TELEGRAM_PASSWORD is invalid. Please enter it again.")
                else:
                    print("Invalid Telegram 2FA password. Please try again.")
                password_candidate = ""
                used_saved_password = False

    try:
        if password_from_env:
            await client.start(phone=phone, password=password_from_env)
        else:
            await client.start(phone=phone)
    except SessionPasswordNeededError:
        await _complete_2fa_login(password_from_env)
    except PasswordHashInvalidError:
        await _complete_2fa_login()
    except AuthKeyUnregisteredError:
        print("Session is invalid. Remove session file and run setup again.")
        raise


async def _fetch_channel_rows(client: TelegramClient) -> list[tuple[int, str, str, str]]:
    dialogs = await client.get_dialogs(limit=None)
    channel_rows: list[tuple[int, str, str, str]] = []

    for dialog in dialogs:
        if not dialog.is_channel:
            continue
        entity = dialog.entity
        title = getattr(entity, "title", dialog.name or "(no title)")
        username = getattr(entity, "username", None)
        entity_id = getattr(entity, "id", None)
        if entity_id is None:
            continue
        access_value = f"@{username}" if username else "(private/no username)"
        channel_rows.append((entity_id, title, access_value, str(entity_id)))

    return channel_rows


async def _run_search_mode(
    client: TelegramClient,
    query: str,
    env_path: Path,
) -> None:
    print("\nLoading channels/groups for search...")
    channel_rows = await _fetch_channel_rows(client)
    save_path = _save_channel_inventory(channel_rows)
    print(f"Saved channel inventory to: {save_path}")

    def _handle_add(tokens: list[str]) -> None:
        merged_value = _append_channels_to_env(env_path, tokens)
        print(f"Updated config.env: TELEGRAM_CHANNEL_ID={merged_value}")

    _run_channel_search_loop(
        channel_rows=channel_rows,
        initial_query=query,
        on_add=_handle_add,
        prompt_label="search",
    )


def _run_channel_search_loop(
    channel_rows: list[tuple[int, str, str, str]],
    initial_query: str = "",
    on_add: Callable[[list[str]], None] | None = None,
    prompt_label: str = "search",
) -> list[str]:
    """Shared interactive channel search/add loop used by setup and search modes."""
    if initial_query.strip():
        _print_filtered_channel_rows(channel_rows, initial_query)
    else:
        _print_channel_rows(channel_rows)

    print("\nSearch commands:")
    print("  - Type any text to search")
    print("  - add <selectors>   (e.g. add 194.  or  add 2,8,-1001234567890)")
    print("  - show              (show all channels)")
    print("  - done              (finish)")

    staged_tokens: list[str] = []

    while True:
        command = input(f"\n{prompt_label}> ").strip()
        if not command:
            continue

        lowered = command.lower()
        if lowered in {"done", "exit", "quit"}:
            break

        if lowered in {"show", "all"}:
            _print_channel_rows(channel_rows)
            continue

        if lowered.startswith("add "):
            selector = command[4:].strip()
            if not selector:
                print("Provide channel selector(s) after 'add'.")
                continue

            try:
                parsed_tokens = _parse_channel_selection_input(selector, channel_rows)
            except ValueError as exc:
                print(f"Invalid input: {exc}")
                continue

            if on_add is not None:
                on_add(parsed_tokens)
            else:
                staged_tokens = _merge_channel_tokens(staged_tokens, parsed_tokens)
                print(f"Staged channel selection: {','.join(staged_tokens)}")
            continue

        _print_filtered_channel_rows(channel_rows, command)

    return staged_tokens


async def run_interactive_setup() -> None:
    """
    Stage 1: Connect/login and create session.
    Stage 2: List channels and optionally write TELEGRAM_CHANNEL_ID to config.env.
    """
    env_path = _project_root() / "config.env"
    env_values = _read_env_values(env_path)

    api_id_raw = (env_values.get("TELEGRAM_API_ID") or "").strip()
    api_hash = (env_values.get("TELEGRAM_API_HASH") or "").strip()
    phone = (env_values.get("TELEGRAM_PHONE") or "").strip()
    password_from_env = (env_values.get("TELEGRAM_PASSWORD") or "").strip()
    session_path = (env_values.get("TELEGRAM_SESSION_PATH") or "data/autotrader.session").strip()
    args = _parse_args()

    if not api_id_raw or not api_hash or not phone:
        raise RuntimeError(
            "Missing TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE in config.env"
        )

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be an integer") from exc

    print("=" * 60)
    print("AutoTrader Telegram Setup")
    print("=" * 60)
    print("Tip: Use --mode search later to find channels without full login flow.")

    selected_mode = args.mode
    if selected_mode is None:
        print("\nSelect mode:")
        print("  1) setup  - login/session + list channels + optional TELEGRAM_CHANNEL_ID update")
        print("  2) search - list/search channels using existing session")
        mode_input = input("Choose 1/2 (default 1): ").strip()
        selected_mode = "search" if mode_input == "2" else "setup"

    client = create_telegram_client(
        api_id=api_id,
        api_hash=api_hash,
        session_path=str(_resolve_session_path(session_path)),
    )

    await _ensure_authorized(
        client=client,
        phone=phone,
        password_from_env=password_from_env,
        env_path=env_path,
    )

    me = await client.get_me()
    print(f"[OK] Telegram connection successful. Logged in as {me.first_name} (id={me.id})")
    print("Session created/updated.")

    if selected_mode == "search":
        await _run_search_mode(client, args.query, env_path)
        await client.disconnect()
        return

    print("\nDiscovering available channels/groups...")
    channel_rows = await _fetch_channel_rows(client)
    save_path = _save_channel_inventory(channel_rows)
    print(f"Saved channel inventory to: {save_path}")

    current_value = (env_values.get("TELEGRAM_CHANNEL_ID") or "").strip()
    if current_value:
        print(f"\nCurrent TELEGRAM_CHANNEL_ID in config.env: {current_value}")

    print("\nSetup mode channel selection.")
    print("Use search + add commands, then type 'done' to save selected values.")

    selected_tokens = _run_channel_search_loop(
        channel_rows=channel_rows,
        initial_query=args.query,
        on_add=None,
        prompt_label="setup",
    )

    if not selected_tokens:
        print("Skipped writing TELEGRAM_CHANNEL_ID.")
        await client.disconnect()
        return

    merged_value = ",".join(selected_tokens)
    _update_env_key(env_path, "TELEGRAM_CHANNEL_ID", merged_value)
    print(f"Updated config.env: TELEGRAM_CHANNEL_ID={merged_value}")

    await client.disconnect()


def main() -> None:
    asyncio.run(run_interactive_setup())


if __name__ == "__main__":
    main()