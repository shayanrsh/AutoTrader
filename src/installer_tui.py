#!/usr/bin/env python3
"""Textual installer UI for AutoTrader."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

TRADER_USER = os.environ.get("AUTOTRADER_USER", "trader")
INSTALL_DIR = Path(os.environ.get("AUTOTRADER_DIR", f"/home/{TRADER_USER}/autotrader"))
INSTALL_SCRIPT = Path(os.environ.get("AUTOTRADER_INSTALL_SCRIPT", Path(__file__).resolve().parents[1] / "install.sh"))
CONFIG_FILE = INSTALL_DIR / "config.env"

FIELDS = [
    ("TELEGRAM_API_ID", "12345678"),
    ("TELEGRAM_API_HASH", ""),
    ("TELEGRAM_PHONE", "+1234567890"),
    ("TELEGRAM_CHANNEL_ID", "-1001234567890"),
    ("NOTIFY_BOT_TOKEN", ""),
    ("NOTIFY_CHAT_ID", "123456789"),
    ("GEMINI_API_KEY", ""),
    ("GROQ_API_KEY", ""),
    ("MT5_ACCOUNT", "12345678"),
    ("MT5_PASSWORD", ""),
    ("MT5_SERVER", "Alpari-MT5"),
    ("DRY_RUN", "true"),
]


class InstallerTUI(App[None]):
    TITLE = "AutoTrader Installer"
    SUB_TITLE = "Textual setup and lifecycle manager"

    CSS = """
    Screen {
      layout: vertical;
      background: #0c1424;
      color: #e2e8f0;
    }

    #root {
      layout: horizontal;
      height: 1fr;
      margin: 1;
    }

    #left {
      width: 2fr;
      border: solid #2563eb;
      padding: 1;
    }

    #right {
      width: 2fr;
      border: solid #0ea5a5;
      padding: 1;
    }

    .cfg {
      margin: 0 0 1 0;
    }

    #actions Button {
      width: 100%;
      margin: 0 0 1 0;
    }

    #logs {
      height: 14;
      margin: 1;
      border: solid #64748b;
      background: #020617;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="root"):
            with Vertical(id="left"):
                yield Label("Installer Actions", classes="cfg")
                with Vertical(id="actions"):
                    yield Button("Full Install", id="full", variant="primary")
                    yield Button("App Only", id="app")
                    yield Button("Update", id="update")
                    yield Button("Validate Config", id="setup")
                    yield Button("Open Dashboard", id="dashboard")
                    yield Button("Uninstall Everything", id="uninstall", variant="error")
                    yield Button("Quit", id="quit")
            with Vertical(id="right"):
                yield Label("Config (saved to /home/trader/autotrader/config.env)", classes="cfg")
                for key, _default in FIELDS:
                    password = key in {"NOTIFY_BOT_TOKEN", "GEMINI_API_KEY", "GROQ_API_KEY", "MT5_PASSWORD"}
                    yield Input(placeholder=key, id=f"cfg_{key}", password=password)
                yield Button("Save Config", id="save_config", variant="success")
        yield Static("Actions run install.sh in non-interactive mode with Textual as the UI layer.", id="hint")
        yield RichLog(id="logs", markup=True, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        self.load_config_values()
        self.log("[bold green]Installer ready.[/]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "save_config":
            self.save_config_values()
            return
        if button_id == "quit":
            self.exit()
            return

        if button_id == "dashboard":
            self.run_cmd(["bash", str(INSTALL_SCRIPT), "--mode", "dashboard", "--no-textual", "--non-interactive", "--assume-yes"])
            return

        if button_id == "setup":
            self.save_config_values()
            self.run_cmd(["bash", str(INSTALL_SCRIPT), "--mode", "setup", "--no-textual", "--non-interactive", "--assume-yes"])
            return

        if button_id in {"full", "app", "update"}:
            self.save_config_values()
            self.run_cmd([
                "bash",
                str(INSTALL_SCRIPT),
                "--mode",
                button_id,
                "--no-textual",
                "--non-interactive",
                "--assume-yes",
                "--skip-config-wizard",
            ])
            return

        if button_id == "uninstall":
            self.run_cmd([
                "bash",
                str(INSTALL_SCRIPT),
                "--mode",
                "uninstall",
                "--no-textual",
                "--non-interactive",
                "--assume-yes",
            ])

    def config_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for key, default in FIELDS:
            widget = self.query_one(f"#cfg_{key}", Input)
            current = widget.value.strip()
            values[key] = current if current else default
        return values

    def load_config_values(self) -> None:
        data: dict[str, str] = {}
        if CONFIG_FILE.exists():
            for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
                if "=" not in line or line.strip().startswith("#"):
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()

        for key, default in FIELDS:
            widget = self.query_one(f"#cfg_{key}", Input)
            widget.value = data.get(key, default)

    def save_config_values(self) -> None:
        values = self.config_values()
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        lines = [f"{k}={v}" for k, v in values.items()]
        CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.log(f"[green]Saved config to {CONFIG_FILE}[/]")

    def run_cmd(self, cmd: list[str]) -> None:
        self.log("$ " + " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=7200)
        except subprocess.TimeoutExpired:
            self.log("[red]Command timed out[/]")
            return

        if result.stdout.strip():
            self.log(result.stdout.rstrip())
        if result.stderr.strip():
            self.log(f"[yellow]{result.stderr.rstrip()}[/]")

        if result.returncode == 0:
            self.log("[bold green]Action completed successfully.[/]")
        else:
            self.log(f"[bold red]Action failed with exit code {result.returncode}[/]")

    def log(self, message: str) -> None:
        self.query_one("#logs", RichLog).write(message)


def main() -> None:
    InstallerTUI().run()


if __name__ == "__main__":
    main()
