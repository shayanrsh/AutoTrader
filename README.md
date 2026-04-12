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

A production-grade, fully automated trading bot that monitors a private Telegram channel for XAUUSD (Gold/USD) trading signals, uses AI to parse unstructured signal text, and executes trades on MetaTrader 5 via Alpari broker — all running headless on an Ubuntu server.

## ⚡ One-Line Install

```bash
curl -fsSL https://raw.githubusercontent.com/shayanrsh/AutoTrader/main/install.sh | sudo bash
```

That's it. The installer handles everything: system packages, Wine, MetaTrader 5, Python environments, firewall, SSH hardening, and systemd services.
It automatically enables the TUI when `whiptail` is available (and attempts to install it), while still supporting interactive text prompts when TUI isn't possible.

## 🏗️ Architecture

```
Telegram Channel ──→ Telethon Listener ──→ asyncio.Queue ──→ AI Signal Parser ──→ Risk Manager ──→ MT5 Executor ──→ Alpari Broker
                                                                    │                                       │
                                                              Gemini Flash                            SQLite DB
                                                              Groq (failover)                      Telegram Notifier
                                                              Regex (fallback)                     Health Check API
```

**Two independent systemd services:**

| Service              | Process                        | Restart Policy            |
| -------------------- | ------------------------------ | ------------------------- |
| `mt5-bridge.service` | Xvfb + Wine MT5 + mt5linux RPC | `Restart=always` (10s)    |
| `autotrader.service` | Main Python trading bot        | `Restart=on-failure` (5s) |

## 🧩 Components

| Module                 | Description                                                         |
| ---------------------- | ------------------------------------------------------------------- |
| `telegram_listener.py` | Telethon-based channel monitor with auto-reconnect and catch-up     |
| `ai_parser.py`         | 3-tier parsing: **Gemini Flash** → **Groq LLaMA 3.3** → **Regex**   |
| `risk_manager.py`      | Per-trade risk cap, position limits, daily loss halt, deduplication |
| `mt5_executor.py`      | Order placement with requote retry, auto-symbol detection           |
| `notifier.py`          | Trade confirmations and error alerts via Telegram Bot API           |
| `database.py`          | Async SQLite for signal history, trade log, deduplication           |
| `health.py`            | HTTP `/health` and `/metrics` endpoints for monitoring              |

## 🛡️ Risk Management

- **Per-trade risk**: Configurable % of balance (default: 1%)
- **XAUUSD lot formula**: `lot = risk_amount / (SL_distance × 100)`
- **Max open trades**: Hard limit on concurrent positions (default: 5)
- **Daily loss limit**: Halts trading when daily loss exceeds threshold (default: 5%)
- **Signal dedup**: SHA-256 hash prevents duplicate executions within a 4-hour window
- **Conflict detection**: Warns on opposing positions for same symbol

## 📦 Project Structure

```
AutoTrader/
├── install.sh                  # ← One-line installer
├── config.env.example          # Configuration template (every field documented)
├── requirements.txt            # Pinned dependencies
├── setup_server.sh             # Full Ubuntu 24.04 setup script
├── src/
│   ├── main.py                 # Async orchestrator (entry point)
│   ├── config.py               # Pydantic Settings loader
│   ├── models.py               # Data models (ParsedSignal, TradeResult, etc.)
│   ├── telegram_listener.py    # Telethon channel monitor
│   ├── ai_parser.py            # AI signal parser with failover
│   ├── regex_parser.py         # Regex fallback parser
│   ├── risk_manager.py         # Risk management + lot sizing
│   ├── mt5_executor.py         # MT5 trade execution via bridge
│   ├── notifier.py             # Telegram bot notifications
│   ├── database.py             # Async SQLite operations
│   ├── health.py               # HTTP health check server
│   └── utils.py                # Logging, hashing, utilities
├── systemd/
│   ├── autotrader.service      # Bot systemd unit (security hardened)
│   ├── mt5-bridge.service      # MT5 bridge systemd unit
│   └── start_mt5_bridge.sh     # Bridge process manager
├── tests/
│   ├── test_parser.py          # Parser unit tests (22 tests)
│   ├── test_risk_manager.py    # Risk manager tests (18 tests)
│   └── sample_signals.txt      # 12 real-world signal samples
└── docs/
    └── DEPLOYMENT.md           # Step-by-step deployment guide
```

## 🔧 Configuration

Copy the template and fill in your values:

```bash
cp config.env.example config.env
chmod 600 config.env
```

### Required API Keys

| Key                            | Where to get it                                                  |
| ------------------------------ | ---------------------------------------------------------------- |
| `TELEGRAM_API_ID` / `API_HASH` | [my.telegram.org/apps](https://my.telegram.org/apps)             |
| `NOTIFY_BOT_TOKEN`             | [@BotFather on Telegram](https://t.me/BotFather)                 |
| `GEMINI_API_KEY`               | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GROQ_API_KEY`                 | [console.groq.com/keys](https://console.groq.com/keys)           |
| `MT5_ACCOUNT` / `MT5_PASSWORD` | Your Alpari MT5 account                                          |

### Key Settings

```env
# Start safe — dry run logs trades without executing
DRY_RUN=true

# Risk settings
MAX_RISK_PER_TRADE_PCT=1.0    # 1% per trade
MAX_OPEN_TRADES=5              # Max concurrent positions
DAILY_LOSS_LIMIT_PCT=5.0       # Stop trading after 5% daily loss
```

## 🚀 Quick Start

### After Installation

```bash
# 1. Edit configuration
sudo -u trader nano /home/trader/autotrader/config.env

# 2. First-time Telegram auth (interactive — enter verification code)
sudo -u trader bash -c 'cd /home/trader/autotrader && source venv/bin/activate && python -m src.main'

# 3. Start services
sudo systemctl start mt5-bridge
sudo systemctl start autotrader

# 4. Monitor
journalctl -u autotrader -f
```

### First-Time MT5 Login

MT5 needs a GUI for initial broker login. Use VNC:

```bash
sudo apt install -y tigervnc-standalone-server
sudo -u trader vncserver :1
# Connect via VNC client → open MT5 → login → close VNC
```

## 📊 Monitoring

### Health Check API

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "uptime_seconds": 3600.5,
  "open_trades": 2,
  "daily_pnl": 45.30,
  "mt5_connected": true,
  "telegram_connected": true,
  "dry_run": false
}
```

### Logs

```bash
journalctl -u autotrader -f          # Bot logs
journalctl -u mt5-bridge -f          # MT5 bridge logs
tail -f /home/trader/autotrader/data/autotrader.log  # App log file
```

## 🧪 Testing

```bash
# Run all tests (40 tests)
cd /home/trader/autotrader
source venv/bin/activate
python -m pytest tests/ -v
```

**Dry-run mode** (`DRY_RUN=true`) parses signals and calculates trades but never executes — perfect for validating the system before going live.

## ⚙️ Installer TUI

The installer now opens an interactive TUI menu (`whiptail` when available, clean text fallback otherwise).

### Core actions

| Action             | What it does                                                          |
| ------------------ | --------------------------------------------------------------------- |
| **Full Install**   | Everything: system packages, Wine, MT5, Python, firewall, services    |
| **App Only**       | Bot code + Python venv (assumes system deps are installed)            |
| **Update Project** | Pull latest code, refresh venv dependencies, optional service restart |

### Extra utility actions

| Action                | What it does                                                  |
| --------------------- | ------------------------------------------------------------- |
| **System Status**     | Shows install path, disk/RAM usage, and service states        |
| **Service Manager**   | Start/stop/restart services, inspect status, view recent logs |
| **Health Check**      | Queries `http://localhost:8080/health`                        |
| **Backup config.env** | Creates timestamped config backups                            |
| **Edit config.env**   | Opens config file directly from the installer                 |

```bash
# Full install (default)
curl -fsSL https://raw.githubusercontent.com/shayanrsh/AutoTrader/main/install.sh | sudo bash

# Or clone and run manually
git clone https://github.com/shayanrsh/AutoTrader.git
cd AutoTrader
sudo bash install.sh
```

## ⚠️ Alpari Notes

- Alpari Micro accounts are **MT4 only** — use Standard or ECN for MT5
- Symbol names vary by account type: `XAUUSD`, `XAUUSDm`, `XAUUSD.`, `XAUUSD#`
- The executor auto-detects the correct symbol suffix on startup
- Common servers: `Alpari-MT5`, `Alpari-MT5-Demo`

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

## ⚠️ Disclaimer

This software is for educational purposes only. Trading forex carries significant risk of loss. Past performance is not indicative of future results. The authors are not responsible for any financial losses incurred through the use of this software. Always test thoroughly with a demo account before risking real capital.
