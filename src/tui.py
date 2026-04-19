#!/usr/bin/env python3
"""Simple terminal dashboard and control center for AutoTrader (no Textual)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

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
CONFIG_FILE = INSTALL_DIR / "config.env"
HEALTH_URL = os.environ.get("AUTOTRADER_HEALTH_URL", "http://localhost:8080/health")
METRICS_URL = os.environ.get("AUTOTRADER_METRICS_URL", "http://localhost:8080/metrics")


def _clear_screen() -> None:
    print("\033[2J\033[H", end="")


def _fetch_json(url: str) -> dict:
    try:
        with urlopen(url, timeout=2) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}


def _service_state(name: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", name],
        capture_output=True,
        text=True,
        check=False,
    )
    state = result.stdout.strip() or result.stderr.strip()
    return state if state else "unknown"


def _run_command(cmd: list[str], *, needs_root: bool = False, timeout: int = 60) -> bool:
    full_cmd = cmd
    if needs_root and os.geteuid() != 0:
        full_cmd = ["sudo", *cmd]

    print("\n$ " + " ".join(full_cmd))
    result = subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )

    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip())

    if result.returncode != 0:
        print(f"\nCommand failed with exit code {result.returncode}")
        return False
    return True


def _service_action(action: str) -> None:
    ok = _run_command(["systemctl", action, "mt5-bridge", "autotrader"], needs_root=True, timeout=30)
    if ok:
        print(f"\nServices {action} completed.")


def _toggle_dry_run() -> None:
    if not CONFIG_FILE.exists():
        print(f"Config file not found: {CONFIG_FILE}")
        return

    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    found = False
    next_value = "true"
    updated: list[str] = []

    for line in lines:
        if line.startswith("DRY_RUN="):
            current = line.split("=", 1)[1].strip().lower()
            next_value = "false" if current == "true" else "true"
            updated.append(f"DRY_RUN={next_value}")
            found = True
        else:
            updated.append(line)

    if not found:
        updated.append("DRY_RUN=true")

    CONFIG_FILE.write_text("\n".join(updated) + "\n", encoding="utf-8")
    print(f"DRY_RUN set to {next_value}")

    _service_action("restart")


def _run_update() -> None:
    if not (INSTALL_DIR / ".git").exists():
        print(f"Git repository not found at {INSTALL_DIR}")
        return

    cmd = [
        "sudo",
        "-u",
        TRADER_USER,
        "bash",
        "-lc",
        (
            f"set -euo pipefail; cd '{INSTALL_DIR}' && "
            "git pull --ff-only origin main && "
            "source venv/bin/activate && pip install -r requirements.txt -q"
        ),
    ]
    if os.geteuid() == 0:
        cmd = cmd[1:]

    ok = _run_command(cmd, timeout=180)
    if ok:
        _service_action("restart")
        print("Update completed and services restarted.")


def _show_logs(unit: str) -> None:
    print(f"\nLast logs for {unit}")
    _run_command(["journalctl", "-u", unit, "-n", "80", "--no-pager"], timeout=30)


def _print_overview() -> None:
    disk = shutil.disk_usage("/")
    disk_free_gb = disk.free // (1024**3)
    disk_total_gb = disk.total // (1024**3)

    ram_line = "n/a"
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            values: dict[str, int] = {}
            for line in handle:
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0])
            total_mb = values.get("MemTotal", 0) // 1024
            avail_mb = values.get("MemAvailable", 0) // 1024
            used_mb = max(total_mb - avail_mb, 0)
            ram_line = f"{used_mb}MB used / {total_mb}MB total"
    except OSError:
        pass

    mt5_status = _service_state("mt5-bridge")
    app_status = _service_state("autotrader")

    health = _fetch_json(HEALTH_URL)
    metrics = _fetch_json(METRICS_URL)

    print("=" * 72)
    print("AutoTrader Runtime Overview")
    print("=" * 72)
    print(f"Install Dir: {INSTALL_DIR}")
    print(f"Config File: {CONFIG_FILE}")
    print(f"Disk      : {disk_free_gb}GB free / {disk_total_gb}GB total")
    print(f"RAM       : {ram_line}")
    print("-" * 72)
    print("Services")
    print(f"  mt5-bridge : {mt5_status}")
    print(f"  autotrader : {app_status}")
    print("-" * 72)
    print("Metrics")
    print(f"  dry_run           : {health.get('dry_run', 'n/a')}")
    print(f"  open_trades       : {health.get('open_trades', 'n/a')}")
    print(f"  daily_pnl         : {health.get('daily_pnl', 'n/a')}")
    print(f"  signals_processed : {metrics.get('signals_processed', 'n/a')}")
    print(f"  trades_executed   : {metrics.get('trades_executed', 'n/a')}")
    print(f"  errors_count      : {metrics.get('errors_count', 'n/a')}")
    print("-" * 72)
    print(f"Health endpoint : {HEALTH_URL}")
    print(f"Metrics endpoint: {METRICS_URL}")
    print("=" * 72)


def _print_menu() -> None:
    print("\nActions")
    print("  1) Refresh Overview")
    print("  2) Start Services")
    print("  3) Stop Services")
    print("  4) Restart Services")
    print("  5) Toggle DRY_RUN")
    print("  6) Update Project")
    print("  7) Show Bot Logs")
    print("  8) Show Bridge Logs")
    print("  9) Run Setup Wizard")
    print("  0) Quit")


def _run_setup_hint() -> None:
    print("\nOpen installer from shell:")
    print("  atinstall   (aliases: ati, autotrader-installer)")


def main() -> None:
    while True:
        _clear_screen()
        _print_overview()
        _print_menu()

        choice = input("\nChoose action: ").strip()

        if choice == "0":
            print("Goodbye.")
            return
        if choice == "1":
            continue
        if choice == "2":
            _service_action("start")
        elif choice == "3":
            _service_action("stop")
        elif choice == "4":
            _service_action("restart")
        elif choice == "5":
            _toggle_dry_run()
        elif choice == "6":
            _run_update()
        elif choice == "7":
            _show_logs("autotrader")
        elif choice == "8":
            _show_logs("mt5-bridge")
        elif choice == "9":
            _run_setup_hint()
        else:
            print("Invalid option. Choose a number from 0 to 9.")

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    main()
