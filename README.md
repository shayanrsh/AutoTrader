<p align="center">
  <h1 align="center">🤖 AutoTrader</h1>
  <p align="center">
    <strong>Automated XAUUSD Forex Trading System</strong><br>
    Telegram Signals → AI Parsing → MetaTrader 5 Execution
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/platform-Ubuntu_24.04-orange?logo=ubuntu&logoColor=white" alt="Ubuntu">
    <img src="https://img.shields.io/badge/MT5-Alpari-green" alt="MetaTrader 5">
    <img src="https://img.shields.io/badge/AI-Gemini_+_Groq-purple" alt="AI">
    <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="License">
  </p>
</p>

---

A production-grade, automated trading bot that monitors a private Telegram channel for XAUUSD signals, parses unstructured text with AI, and executes orders on MT5 via Alpari.

## ⚡ One-Line Install

```bash
curl -fsSL https://raw.githubusercontent.com/shayanrsh/AutoTrader/main/install.sh | sudo bash
```

The installer now includes:

- Interactive setup wizard for all required credentials (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `MT5_ACCOUNT`, etc.)
- Guided hints on where each credential is obtained
- Modern Textual dashboard UI (with shell fallback when unavailable)
- Modern Textual installer UI for setup/update/uninstall workflows
- Better failure reporting and safer preflight behavior
- Rerun-safe virtual environment setup (second install/update runs no longer fail on existing `venv`)
- Runtime dashboard command installation (`atdash`, alias `atd`)

## 🏗️ Architecture

```
Telegram Channel ──→ Telethon Listener ──→ asyncio.Queue ──→ AI Signal Parser ──→ Risk Manager ──→ MT5 Executor ──→ Alpari Broker
                                                                    │                                       │
                                                              Gemini Flash                            SQLite DB
                                                              Groq (failover)                      Telegram Notifier
                                                              Regex (fallback)                     Health Check API
```

## 🧩 Components

| Module                            | Description                                                         |
| --------------------------------- | ------------------------------------------------------------------- |
| `src/telegram_listener.py`        | Telethon-based channel monitor with reconnect and catch-up          |
| `src/ai_parser.py`                | 3-tier parsing: Gemini → Groq → Regex fallback                      |
| `src/risk_manager.py`             | Per-trade risk cap, position limits, daily loss halt, deduplication |
| `src/mt5_executor.py`             | MT5 trade execution with retry and symbol auto-detection            |
| `src/notifier.py`                 | Telegram notifications for alerts/trades/errors                     |
| `src/database.py`                 | Async SQLite persistence                                            |
| `src/health.py`                   | HTTP `/health` and `/metrics` endpoints                             |
| `scripts/autotrader-dashboard.sh` | Runtime control dashboard installed as `atdash`                     |

## ⚙️ Installer UX

### Main actions

- **Full Install**: system deps + Wine + MT5 + app + services
- **App Only**: app + venv + services (assumes system deps already present)
- **Update Project**: git pull + dependency refresh + optional restart
- **Setup Wizard**: re-run guided config onboarding
- **Dashboard**: launch runtime dashboard
- **Uninstall Everything**: removes services, commands, files, user, and cache

### Setup wizard fields (interactive)

- Telegram listener: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_CHANNEL_ID`
- Notify bot: `NOTIFY_BOT_TOKEN`, `NOTIFY_CHAT_ID`
- AI keys: `GEMINI_API_KEY`, `GROQ_API_KEY`
- MT5 credentials: `MT5_ACCOUNT`, `MT5_PASSWORD`, `MT5_SERVER`
- Runtime safety: `DRY_RUN`

### Preflight behavior

- Hard fail only at very low disk (`<2GB`)
- At low-but-usable disk (`2GB–5GB`), installer warns and lets you continue
- Clear command + line context when installer errors occur

## 📊 Dashboard (short command)

After install:

```bash
atdash
# alias:
atd

# installer command:
atinstall
# aliases:
ati
autotrader-installer
```

Dashboard includes:

- Live overview (service states + runtime stats from health/metrics)
- Start/stop/restart services
- DRY_RUN toggle with bot restart
- Update + restart flow
- Quick log access for `autotrader` and `mt5-bridge`
- Keyboard shortcuts in TUI (`q` quit, `r` refresh, `l` bot logs, `b` bridge logs)

Installer command names:

- `atinstall` (aliases: `ati`, `autotrader-installer`)
- `atdash` (aliases: `atd`, `autotrader-dashboard`)

## 🚀 Quick Start

```bash
# 1) Install
curl -fsSL https://raw.githubusercontent.com/shayanrsh/AutoTrader/main/install.sh | sudo bash

# 2) First Telegram authentication (one-time interactive)
sudo -u trader bash -c 'cd /home/trader/autotrader && source venv/bin/activate && python -m src.main'

# 3) Start services
sudo systemctl start mt5-bridge
sudo systemctl start autotrader

# 4) Open dashboard
atdash
```

## 📦 Project Structure

```
AutoTrader/
├── install.sh
├── config.env.example
├── requirements.txt
├── setup_server.sh
├── scripts/
│   └── autotrader-dashboard.sh
├── src/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── telegram_listener.py
│   ├── ai_parser.py
│   ├── regex_parser.py
│   ├── risk_manager.py
│   ├── mt5_executor.py
│   ├── notifier.py
│   ├── database.py
│   ├── health.py
│   └── utils.py
├── systemd/
│   ├── autotrader.service
│   ├── mt5-bridge.service
│   └── start_mt5_bridge.sh
├── tests/
│   ├── test_parser.py
│   ├── test_risk_manager.py
│   └── sample_signals.txt
└── docs/
    └── DEPLOYMENT.md
```

## ⚠️ Notes

- Alpari Micro accounts are MT4-only; use Standard/ECN for MT5.
- Symbol variants can differ (`XAUUSD`, `XAUUSDm`, `XAUUSD.`, `XAUUSD#`).
- Always start with `DRY_RUN=true` and validate before live execution.

## 📄 License

MIT License — see [LICENSE](LICENSE).

## ⚠️ Disclaimer

This project is for educational purposes only. Trading forex carries significant risk. Test thoroughly on demo before using real capital.
