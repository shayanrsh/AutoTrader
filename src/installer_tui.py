#!/usr/bin/env python3
"""Simple interactive installer UI for AutoTrader (no Textual dependency)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

TRADER_USER = os.environ.get("AUTOTRADER_USER", "trader")


def _default_install_dir() -> Path:
    env_dir = os.environ.get("AUTOTRADER_DIR")
    if env_dir:
        return Path(env_dir)

    project_dir = Path(__file__).resolve().parents[1]
    if (project_dir / "install.sh").exists():
        return project_dir

    return Path(f"/home/{TRADER_USER}/autotrader")


INSTALL_DIR = _default_install_dir()
INSTALL_SCRIPT = Path(
    os.environ.get("AUTOTRADER_INSTALL_SCRIPT", str(INSTALL_DIR / "install.sh"))
)
CONFIG_FILE = INSTALL_DIR / "config.env"

FIELDS = [
    ("TELEGRAM_API_ID", "12345678", False),
    ("TELEGRAM_API_HASH", "", False),
    ("TELEGRAM_PHONE", "+1234567890", False),
    ("TELEGRAM_CHANNEL_ID", "-1001234567890", False),
    ("NOTIFY_BOT_TOKEN", "", True),
    ("NOTIFY_CHAT_ID", "123456789", False),
    ("GEMINI_API_KEY", "", True),
    ("XAI_API_KEY", "", True),
    ("MT5_ACCOUNT", "12345678", False),
    ("MT5_PASSWORD", "", True),
    ("MT5_SERVER", "Alpari-MT5", False),
    ("DRY_RUN", "true", False),
]


def _mask_value(value: str, secret: bool) -> str:
    if not secret:
        return value
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + ("*" * (len(value) - 4)) + value[-2:]


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        parsed = value.strip()
        if parsed.startswith('"') and parsed.endswith('"') and len(parsed) >= 2:
            parsed = parsed[1:-1].replace(r'\"', '"').replace(r"\\", "\\")

        values[key.strip()] = parsed

    return values


def _format_env_value(value: str) -> str:
    if not value:
        return ""

    needs_quotes = any(ch in value for ch in ('#', ' ', '\t', '"'))
    if not needs_quotes:
        return value

    escaped = value.replace("\\", r"\\").replace('"', r'\"')
    return f'"{escaped}"'


def _save_config_values(values: dict[str, str]) -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    lines: list[str]
    if CONFIG_FILE.exists():
        lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    out: list[str] = []
    replaced_keys: set[str] = set()
    known_keys = {key for key, _default, _secret in FIELDS}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue

        key, _old = line.split("=", 1)
        key = key.strip()
        if key in known_keys:
            out.append(f"{key}={_format_env_value(values.get(key, ''))}")
            replaced_keys.add(key)
        else:
            out.append(line)

    for key, _default, _secret in FIELDS:
        if key not in replaced_keys:
            out.append(f"{key}={_format_env_value(values.get(key, ''))}")

    CONFIG_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _print_header(values: dict[str, str]) -> None:
    disk = shutil.disk_usage("/")
    free_gb = disk.free // (1024**3)
    total_gb = disk.total // (1024**3)

    print("=" * 72)
    print("AutoTrader Installer")
    print("=" * 72)
    print(f"Install dir : {INSTALL_DIR}")
    print(f"Install sh  : {INSTALL_SCRIPT}")
    print(f"Config file : {CONFIG_FILE}")
    print(f"Disk        : {free_gb}GB free / {total_gb}GB total")
    print("-" * 72)

    for key, default, secret in FIELDS:
        current = values.get(key, default)
        print(f"{key:20} = {_mask_value(current, secret)}")
    print("=" * 72)


def _edit_config(values: dict[str, str]) -> None:
    print("\nEdit config values (Enter keeps current value):")
    for key, default, secret in FIELDS:
        current = values.get(key, default)
        shown = _mask_value(current, secret)
        prompt = f"{key} [{shown}]: " if shown else f"{key}: "
        entered = input(prompt).strip()
        if entered:
            values[key] = entered
        elif key not in values:
            values[key] = default


def _run_action(mode: str, values: dict[str, str]) -> int:
    if mode in {"full", "app", "update", "setup"}:
        _save_config_values(values)
        print(f"Saved config to {CONFIG_FILE}")

    cmd = [
        "bash",
        str(INSTALL_SCRIPT),
        "--mode",
        mode,
        "--no-textual",
        "--non-interactive",
        "--assume-yes",
    ]
    if mode in {"full", "app", "update"}:
        cmd.append("--skip-config-wizard")

    print("\n$ " + " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"Failed to launch action: {exc}")
        return 1

    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip())

    rc = proc.wait()
    if rc == 0:
        print(f"\n{mode} completed successfully.")
    else:
        print(f"\n{mode} failed with exit code {rc}.")
    return rc


def _menu_choice() -> str:
    print("\nActions:")
    print("  1) Full Install")
    print("  2) App Only")
    print("  3) Update")
    print("  4) Validate Config")
    print("  5) Open Dashboard")
    print("  6) Edit Config")
    print("  7) Save Config")
    print("  8) Uninstall Everything")
    print("  9) Refresh Summary")
    print("  0) Quit")
    return input("\nChoose action: ").strip()


def main() -> None:
    if not INSTALL_SCRIPT.exists():
        raise SystemExit(f"Installer script not found: {INSTALL_SCRIPT}")

    values = {key: default for key, default, _secret in FIELDS}
    values.update(_read_env_values(CONFIG_FILE))

    while True:
        print("\n" * 2)
        _print_header(values)
        choice = _menu_choice()

        if choice == "0":
            print("Goodbye.")
            return

        if choice == "1":
            _run_action("full", values)
        elif choice == "2":
            _run_action("app", values)
        elif choice == "3":
            _run_action("update", values)
        elif choice == "4":
            _run_action("setup", values)
        elif choice == "5":
            _run_action("dashboard", values)
        elif choice == "6":
            _edit_config(values)
        elif choice == "7":
            _save_config_values(values)
            print(f"Saved config to {CONFIG_FILE}")
        elif choice == "8":
            token = input("Type UNINSTALL to confirm: ").strip()
            if token != "UNINSTALL":
                print("Uninstall blocked. Confirmation token did not match.")
                continue
            _run_action("uninstall", values)
        elif choice == "9":
            values.update(_read_env_values(CONFIG_FILE))
            print("Summary refreshed.")
        else:
            print("Invalid option. Choose a number from 0 to 9.")

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    main()
