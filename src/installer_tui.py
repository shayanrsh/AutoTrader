#!/usr/bin/env python3
"""Enhanced Textual installer UI for AutoTrader."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

TRADER_USER = os.environ.get("AUTOTRADER_USER", "trader")
INSTALL_DIR = Path(os.environ.get("AUTOTRADER_DIR", f"/home/{TRADER_USER}/autotrader"))
INSTALL_SCRIPT = Path(
    os.environ.get("AUTOTRADER_INSTALL_SCRIPT", Path(__file__).resolve().parents[1] / "install.sh")
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


class InstallerTUI(App[None]):
    TITLE = "AutoTrader Installer"
    SUB_TITLE = "Modern setup, update, and removal"

    CSS = """
    Screen {
      layout: vertical;
      background: #071226;
      color: #e2e8f0;
    }
    #hero {
      height: 3;
      margin: 1 2 0 2;
      border: solid #2563eb;
      content-align: center middle;
      background: #0b1a34;
      color: #bfdbfe;
    }
    #summary {
      height: 3;
      margin: 0 2 1 2;
      border: solid #0ea5a5;
      background: #0c1f2f;
      padding: 0 1;
    }
    #root {
      layout: horizontal;
      height: 1fr;
      margin: 0 2;
    }
    #left {
      width: 36;
      border: solid #22c55e;
      padding: 1;
      background: #0d2018;
    }
    #right {
      width: 1fr;
      border: solid #38bdf8;
      padding: 1;
      background: #0a1828;
    }
    #actions Button {
      width: 100%;
      margin: 0 0 1 0;
    }
    .cfg {
      margin: 0 0 1 0;
    }
    #logs {
      height: 16;
      margin: 1 2 1 2;
      border: solid #64748b;
      background: #020617;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "save_config", "Save Config"),
        ("r", "refresh_summary", "Refresh Summary"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Configure once, run actions safely, review full logs.", id="hero")
        yield Static("Loading system summary...", id="summary")
        with Horizontal(id="root"):
            with Vertical(id="left"):
                yield Label("Actions", classes="cfg")
                with Vertical(id="actions"):
                    yield Button("Full Install", id="full", variant="primary")
                    yield Button("App Only", id="app")
                    yield Button("Update", id="update")
                    yield Button("Validate Config", id="setup")
                    yield Button("Open Dashboard", id="dashboard")
                    yield Button("Uninstall Everything", id="uninstall", variant="error")
                    yield Button("Quit", id="quit")
            with Vertical(id="right"):
                yield Label("Config", classes="cfg")
                for key, default, secret in FIELDS:
                    yield Input(value="", placeholder=f"{key} ({default})", id=f"cfg_{key}", password=secret)
                yield Input(value="", placeholder="Type UNINSTALL to enable uninstall", id="uninstall_confirm")
                yield Button("Save Config", id="save_config", variant="success")
        yield RichLog(id="logs", markup=True, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_summary()
        self.load_config_values()
        self.log("[bold green]Installer ready.[/]")

    def action_save_config(self) -> None:
        self.save_config_values()

    def action_refresh_summary(self) -> None:
        self.refresh_summary()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "save_config":
            self.save_config_values()
            return
        if button_id == "quit":
            self.exit()
            return
        if button_id in {"full", "app", "update", "setup", "dashboard", "uninstall"}:
            self.run_action(button_id)

    def refresh_summary(self) -> None:
        disk = shutil.disk_usage("/")
        free_gb = disk.free // (1024 ** 3)
        total_gb = disk.total // (1024 ** 3)
        summary = (
            f"Install path: {INSTALL_DIR} | Script: {INSTALL_SCRIPT} | Disk: {free_gb}GB free / {total_gb}GB total"
        )
        self.query_one("#summary", Static).update(summary)

    def run_action(self, mode: str) -> None:
        if mode == "uninstall":
            token = self.query_one("#uninstall_confirm", Input).value.strip()
            if token != "UNINSTALL":
                self.log("[red]Uninstall blocked. Type UNINSTALL in confirmation field first.[/]")
                return

        if mode in {"full", "app", "update", "setup"}:
            self.save_config_values()

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

        self.log("$ " + " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.log(f"[red]Failed to launch action: {exc}[/]")
            return

        assert proc.stdout is not None
        for line in proc.stdout:
            self.log(line.rstrip())

        rc = proc.wait()
        if rc == 0:
            self.log(f"[bold green]{mode} completed successfully.[/]")
        else:
            self.log(f"[bold red]{mode} failed with exit code {rc}[/]")

    def load_config_values(self) -> None:
        values = {key: default for key, default, _secret in FIELDS}
        if CONFIG_FILE.exists():
            for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
                if "=" not in line or line.strip().startswith("#"):
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()

        for key, _default, _secret in FIELDS:
            self.query_one(f"#cfg_{key}", Input).value = values.get(key, "")

    def save_config_values(self) -> None:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        lines = []
        for key, default, _secret in FIELDS:
            value = self.query_one(f"#cfg_{key}", Input).value.strip() or default
            lines.append(f"{key}={value}")

        CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.log(f"[green]Saved config to {CONFIG_FILE}[/]")

    def log(self, message: str) -> None:
        self.query_one("#logs", RichLog).write(message)


def main() -> None:
    if not INSTALL_SCRIPT.exists():
        raise SystemExit(f"Installer script not found: {INSTALL_SCRIPT}")
    InstallerTUI().run()


if __name__ == "__main__":
    main()
