#!/usr/bin/env python3
"""Modern Textual dashboard and control center for AutoTrader."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, RichLog, Static

TRADER_USER = os.environ.get("AUTOTRADER_USER", "trader")
INSTALL_DIR = Path(os.environ.get("AUTOTRADER_DIR", f"/home/{TRADER_USER}/autotrader"))
CONFIG_FILE = INSTALL_DIR / "config.env"
HEALTH_URL = os.environ.get("AUTOTRADER_HEALTH_URL", "http://localhost:8080/health")
METRICS_URL = os.environ.get("AUTOTRADER_METRICS_URL", "http://localhost:8080/metrics")


class AutoTraderTUI(App[None]):
    TITLE = "AutoTrader Control Center"
    SUB_TITLE = "Modern runtime dashboard"

    CSS = """
    Screen {
      layout: vertical;
      background: #0f1726;
      color: #e2e8f0;
    }

    #top {
      height: 18;
      layout: horizontal;
      margin: 1 2;
    }

    #overview {
      width: 2fr;
      padding: 1 2;
      border: solid #3b82f6;
      background: #111827;
    }

    #actions {
      width: 1fr;
      padding: 1;
      border: solid #10b981;
      background: #0b1323;
    }

    #actions Button {
      width: 100%;
      margin: 0 0 1 0;
    }

    #logs {
      height: 1fr;
      margin: 0 2 1 2;
      border: solid #64748b;
      background: #020617;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("l", "bot_logs", "Bot Logs"),
        ("b", "bridge_logs", "Bridge Logs"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="top"):
            yield Static("Loading runtime data...", id="overview")
            with Vertical(id="actions"):
                yield Button("Refresh Overview", id="refresh", variant="primary")
                yield Button("Start Services", id="start", variant="success")
                yield Button("Stop Services", id="stop", variant="warning")
                yield Button("Restart Services", id="restart")
                yield Button("Toggle DRY_RUN", id="toggle_dry")
                yield Button("Update Project", id="update")
                yield Button("Show Bot Logs", id="logs_bot")
                yield Button("Show Bridge Logs", id="logs_bridge")
                yield Button("Run Setup Wizard", id="setup")
                yield Button("Quit", id="quit", variant="error")
        yield RichLog(id="logs", highlight=True, markup=True, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(5.0, self.refresh_overview)
        self.refresh_overview()
        self.log_line("[bold green]Control center ready.[/]")

    def action_refresh(self) -> None:
        self.refresh_overview()
        self.log_line("Overview refreshed")

    def action_bot_logs(self) -> None:
        self.show_logs("autotrader")

    def action_bridge_logs(self) -> None:
        self.show_logs("mt5-bridge")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "refresh":
            self.action_refresh()
        elif button_id == "start":
            self.service_action("start")
        elif button_id == "stop":
            self.service_action("stop")
        elif button_id == "restart":
            self.service_action("restart")
        elif button_id == "toggle_dry":
            self.toggle_dry_run()
        elif button_id == "update":
            self.run_update()
        elif button_id == "logs_bot":
            self.show_logs("autotrader")
        elif button_id == "logs_bridge":
            self.show_logs("mt5-bridge")
        elif button_id == "setup":
            self.run_setup_wizard()
        elif button_id == "quit":
            self.exit()

    def refresh_overview(self) -> None:
        overview = self.query_one("#overview", Static)
        disk = shutil.disk_usage("/")
        disk_free_gb = disk.free // (1024 ** 3)
        disk_total_gb = disk.total // (1024 ** 3)

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

        mt5_status = self.service_state("mt5-bridge")
        app_status = self.service_state("autotrader")

        health = self.fetch_json(HEALTH_URL)
        metrics = self.fetch_json(METRICS_URL)

        content = (
            "AutoTrader Runtime Overview\n\n"
            f"Install Dir: {INSTALL_DIR}\n"
            f"Disk: {disk_free_gb}GB free / {disk_total_gb}GB total\n"
            f"RAM: {ram_line}\n\n"
            "Services\n"
            f"- mt5-bridge: {mt5_status}\n"
            f"- autotrader: {app_status}\n\n"
            "Metrics\n"
            f"- dry_run: {health.get('dry_run', 'n/a')}\n"
            f"- open_trades: {health.get('open_trades', 'n/a')}\n"
            f"- daily_pnl: {health.get('daily_pnl', 'n/a')}\n"
            f"- signals_processed: {metrics.get('signals_processed', 'n/a')}\n"
            f"- trades_executed: {metrics.get('trades_executed', 'n/a')}\n"
            f"- errors_count: {metrics.get('errors_count', 'n/a')}\n\n"
            f"Health endpoint: {HEALTH_URL}"
        )
        overview.update(content)

    def fetch_json(self, url: str) -> dict:
        try:
            with urlopen(url, timeout=2) as response:  # nosec B310
                return json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, json.JSONDecodeError, OSError):
            return {}

    def service_state(self, name: str) -> str:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip() or result.stderr.strip()
        return state if state else "unknown"

    def service_action(self, action: str) -> None:
        cmd = ["systemctl", action, "mt5-bridge", "autotrader"]
        ok = self.run_command(cmd, needs_root=True, timeout=30)
        if ok:
            self.log_line(f"[bold green]Services {action} completed.[/]")
        self.refresh_overview()

    def toggle_dry_run(self) -> None:
        if not CONFIG_FILE.exists():
            self.log_line(f"[bold red]Config file not found: {CONFIG_FILE}[/]")
            return

        lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
        current = None
        found = False
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
            next_value = "true"
            updated.append("DRY_RUN=true")

        CONFIG_FILE.write_text("\n".join(updated) + "\n", encoding="utf-8")
        self.log_line(f"[bold yellow]DRY_RUN set to {next_value}[/]")
        self.service_action("restart")

    def run_update(self) -> None:
        if not (INSTALL_DIR / ".git").exists():
            self.log_line(f"[bold red]Git repository not found at {INSTALL_DIR}[/]")
            return

        cmd = [
            "sudo",
            "-u",
            TRADER_USER,
            "bash",
            "-lc",
            f"set -euo pipefail; cd '{INSTALL_DIR}' && git pull --ff-only origin main && source venv/bin/activate && pip install -r requirements.txt -q",
        ]
        if os.geteuid() == 0:
            cmd = cmd[1:]

        ok = self.run_command(cmd, needs_root=False, timeout=180)
        if ok:
            self.service_action("restart")
            self.log_line("[bold green]Update completed and services restarted.[/]")
        self.refresh_overview()

    def run_setup_wizard(self) -> None:
        script = INSTALL_DIR / "install.sh"
        if not script.exists():
            self.log_line(f"[bold red]Installer not found at {script}[/]")
            return

        self.log_line("Setup wizard should be run in an interactive shell:")
        self.log_line(f"  sudo bash {script}  # then choose 'setup'")

    def show_logs(self, unit: str) -> None:
        self.log_line(f"[bold cyan]Last logs for {unit}[/]")
        self.run_command(["journalctl", "-u", unit, "-n", "80", "--no-pager"], needs_root=False, timeout=30)

    def run_command(self, cmd: list[str], needs_root: bool, timeout: int) -> bool:
        full_cmd = cmd
        if needs_root and os.geteuid() != 0:
            full_cmd = ["sudo", *cmd]

        self.log_line("$ " + " ".join(full_cmd))
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

        if result.stdout.strip():
            self.log_line(result.stdout.rstrip())
        if result.stderr.strip():
            self.log_line(f"[red]{result.stderr.rstrip()}[/]")

        if result.returncode != 0:
            self.log_line(f"[bold red]Command failed with exit code {result.returncode}[/]")
            return False

        return True

    def log_line(self, message: str) -> None:
        self.query_one("#logs", RichLog).write(message)


def main() -> None:
    app = AutoTraderTUI()
    app.run()


if __name__ == "__main__":
    main()
