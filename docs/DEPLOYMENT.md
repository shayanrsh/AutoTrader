# AutoTrader — Deployment Guide

Complete step-by-step guide for deploying the AutoTrader system on Ubuntu 24.04 LTS.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Server Provisioning](#server-provisioning)
3. [Automated Server Setup](#automated-server-setup)
4. [API Key Acquisition](#api-key-acquisition)
5. [Configuration](#configuration)
6. [Initial MT5 Login (GUI Required)](#initial-mt5-login)
7. [First-Time Telegram Authentication](#first-time-telegram-auth)
8. [Testing with Demo Account](#demo-testing)
9. [Starting the Services](#starting-services)
10. [Monitoring & Logs](#monitoring)
11. [Go-Live Checklist](#go-live-checklist)
12. [Troubleshooting](#troubleshooting)

---

## 1. Prerequisites <a name="prerequisites"></a>

- **Ubuntu 24.04 LTS** server (VPS/cloud) with:
  - Minimum 2 CPU cores
  - 4 GB RAM
  - 20 GB disk
  - Public IPv4 address
- **Alpari MT5 account** (Standard or ECN — NOT Micro, which is MT4-only)
- **Telegram account** with access to the signal channel
- **SSH access** to the server

### Recommended VPS Providers
- Hetzner (€4-8/month for suitable specs)
- DigitalOcean ($6-12/month)
- Vultr ($5-10/month)
- Contabo (€5-8/month, good value)

---

## 2. Server Provisioning <a name="server-provisioning"></a>

### Initial Server Access

```bash
# SSH into your server
ssh root@YOUR_SERVER_IP

# Create a non-root user for SSH (not the trader user — that's for the bot)
adduser deploy
usermod -aG sudo deploy

# Setup SSH key for the deploy user
mkdir -p /home/deploy/.ssh
# Copy your public key
echo "YOUR_PUBLIC_SSH_KEY" >> /home/deploy/.ssh/authorized_keys
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
```

### Upload Project Files

```bash
# From your local machine, upload the project
scp -r ./AutoTrader deploy@YOUR_SERVER_IP:/home/deploy/autotrader-source

# On the server, move to installation location
ssh deploy@YOUR_SERVER_IP
sudo mkdir -p /home/trader
sudo cp -r /home/deploy/autotrader-source/* /home/trader/autotrader/ 2>/dev/null || true
```

---

## 3. Automated Server Setup <a name="automated-server-setup"></a>

```bash
# Run the setup script (as root)
sudo bash /home/trader/autotrader/setup_server.sh
```

This script will:
- ✅ Update system packages
- ✅ Install Python 3.11+
- ✅ Install Wine (32-bit and 64-bit)
- ✅ Install Xvfb (virtual display)
- ✅ Create the `trader` user
- ✅ Initialize Wine environment
- ✅ Download and install MetaTrader 5
- ✅ Install Windows Python in Wine
- ✅ Install MT5 + mt5linux packages in Wine Python
- ✅ Create Python virtual environment for the bot
- ✅ Configure UFW firewall
- ✅ Harden SSH
- ✅ Install fail2ban
- ✅ Install systemd services

**Expected duration: 10-20 minutes**

### Install Bot Dependencies

```bash
sudo -u trader bash -c '
    cd /home/trader/autotrader
    source venv/bin/activate
    pip install -r requirements.txt
'
```

---

## 4. API Key Acquisition <a name="api-key-acquisition"></a>

### Telegram API Credentials (for channel listener)

1. Go to https://my.telegram.org/apps
2. Log in with your phone number
3. Click **"API development tools"**
4. Fill in:
   - App title: `AutoTrader` (anything)
   - Short name: `autotrader` (anything)
   - Platform: `Desktop`
5. Copy **api_id** (integer) and **api_hash** (32-char hex string)

### Telegram Bot Token (for notifications)

1. Open Telegram and message **@BotFather**
2. Send `/newbot`
3. Follow prompts to create a new bot
4. Copy the **bot token** (format: `7000000000:AAF-xxxx...`)
5. Start a chat with your new bot (send `/start`)
6. Get your **chat ID**: message **@userinfobot** → it replies with your ID

### Google Gemini API Key

1. Go to https://aistudio.google.com/apikey
2. Sign in with your Google account
3. Click **"Create API Key"**
4. Select or create a project
5. Copy the API key (starts with `AIzaSy-`)

### Groq API Key

1. Go to https://console.groq.com/keys
2. Sign up / log in
3. Click **"Create API Key"**
4. Name it `autotrader`
5. Copy the key (starts with `gsk_`)

### Find Your Telegram Channel ID

1. Forward any message from the signal channel to **@userinfobot**
2. The bot replies with the channel's numeric ID (negative number, e.g., `-1001234567890`)
3. Alternatively: if the channel has a username, you can use that instead

---

## 5. Configuration <a name="configuration"></a>

```bash
# Create config from template
sudo -u trader bash -c '
    cd /home/trader/autotrader
    cp config.env.example config.env
    chmod 600 config.env
    nano config.env
'
```

Fill in ALL the fields. Critical ones:

```env
# Telegram listener
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_PHONE=+1234567890
TELEGRAM_CHANNEL_ID=-1001234567890

# Notification bot
NOTIFY_BOT_TOKEN=7000000000:AAF-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTIFY_CHAT_ID=123456789

# AI parsers
GEMINI_API_KEY=AIzaSy-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# MT5
MT5_ACCOUNT=12345678
MT5_PASSWORD=YourPassword
MT5_SERVER=Alpari-MT5

# Start in dry-run mode!
DRY_RUN=true
```

---

## 6. Initial MT5 Login (GUI Required) <a name="initial-mt5-login"></a>

MetaTrader 5 requires a GUI for the first login (to accept terms, enter credentials, and connect to the broker). After the first login, it saves the credentials and can run headless.

### Option A: VNC (Recommended)

```bash
# Install TigerVNC
sudo apt install -y tigervnc-standalone-server dbus-x11

# Start VNC as the trader user
sudo -u trader bash -c '
    export USER=trader
    export HOME=/home/trader
    vncpasswd <<< "$(echo -e \"temppass\ntemppass\nn\")"
    vncserver :1 -geometry 1280x1024 -depth 24
'

# Connect via VNC client (on your local machine):
# Server: YOUR_SERVER_IP:5901
# Password: temppass
```

In the VNC session:
1. Open a terminal
2. Run: `WINEPREFIX=/home/trader/.wine wine "/home/trader/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"`
3. MT5 opens → Enter your Alpari credentials
4. Wait for it to connect and sync
5. Close MT5
6. Kill VNC: `vncserver -kill :1`

### Option B: SSH X11 Forwarding (From Linux/macOS)

```bash
ssh -X trader@YOUR_SERVER_IP
WINEPREFIX=~/.wine wine "~/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"
```

---

## 7. First-Time Telegram Authentication <a name="first-time-telegram-auth"></a>

Telethon needs to authenticate interactively the first time (to send you a verification code):

```bash
sudo -u trader bash -c '
    cd /home/trader/autotrader
    source venv/bin/activate
    python -m src.main
'
```

You'll be prompted:
1. Enter phone number (if not in config)
2. Enter the verification code sent to your Telegram
3. Enter 2FA password (if enabled)

After authentication, the session is saved to `data/autotrader.session`. Subsequent starts won't require re-authentication.

**Press Ctrl+C after successful auth** if running in dry-run mode for testing.

---

## 8. Testing with Demo Account <a name="demo-testing"></a>

### Step 1: Dry-Run Mode

With `DRY_RUN=true` in config.env:

```bash
sudo -u trader bash -c '
    cd /home/trader/autotrader
    source venv/bin/activate
    python -m src.main
'
```

Watch the output:
- ✅ Telegram connects and receives messages
- ✅ AI parser extracts signal data correctly
- ✅ Risk manager calculates lot sizes
- ✅ "DRY RUN: Would place..." messages appear
- ✅ Notification bot sends alerts to you

### Step 2: Run Unit Tests

```bash
sudo -u trader bash -c '
    cd /home/trader/autotrader
    source venv/bin/activate
    python -m pytest tests/ -v
'
```

### Step 3: Demo Account Live Test

1. Create an Alpari MT5 demo account (from alpari.com)
2. Update config.env with demo credentials
3. Set `DRY_RUN=false`
4. Run for 24-48 hours monitoring behavior

---

## 9. Starting the Services <a name="starting-services"></a>

```bash
# Start MT5 bridge first
sudo systemctl start mt5-bridge
sleep 30  # Wait for MT5 to initialize

# Start the trading bot
sudo systemctl start autotrader

# Check status
sudo systemctl status mt5-bridge
sudo systemctl status autotrader
```

### Verify Services Survive Reboot

```bash
# Enable auto-start on boot
sudo systemctl enable mt5-bridge
sudo systemctl enable autotrader

# Test with a reboot
sudo reboot
# After reboot, check:
sudo systemctl status mt5-bridge autotrader
```

---

## 10. Monitoring & Logs <a name="monitoring"></a>

### View Logs

```bash
# Real-time bot logs
journalctl -u autotrader -f

# Real-time MT5 bridge logs
journalctl -u mt5-bridge -f

# Application log file
tail -f /home/trader/autotrader/data/autotrader.log
```

### Health Check

```bash
# From the server
curl http://localhost:8080/health

# From outside (if port is open)
curl http://YOUR_SERVER_IP:8080/health
```

### Check Database

```bash
sudo -u trader sqlite3 /home/trader/autotrader/data/autotrader.db "
    SELECT * FROM signals ORDER BY created_at DESC LIMIT 10;
"
```

---

## 11. Go-Live Checklist <a name="go-live-checklist"></a>

Before switching from demo to live trading:

### Functionality
- [ ] Demo account tested for 48+ hours with zero critical errors
- [ ] All SL/TP levels verified against original signals
- [ ] Lot sizes match the configured risk percentage
- [ ] Daily loss limit triggers correctly (test by setting a low limit)
- [ ] Duplicate signal rejection confirmed working
- [ ] Notification bot sends accurate trade/error alerts
- [ ] MT5 bridge auto-restarts after simulated crash (`kill` the process)
- [ ] Bot auto-restarts via systemd after simulated crash
- [ ] Both services survive server reboot

### Security
- [ ] `config.env` has `chmod 600` (readable only by trader user)
- [ ] SSH is key-only authentication (PasswordAuthentication no)
- [ ] Firewall allows only SSH and outbound traffic
- [ ] fail2ban is active
- [ ] .session file is backed up securely

### Configuration
- [ ] `DRY_RUN=false` in config.env
- [ ] `MT5_ACCOUNT` set to live account number
- [ ] `MT5_PASSWORD` set to live password
- [ ] `MT5_SERVER` set to live server (e.g., `Alpari-MT5`, not demo)
- [ ] Risk settings reviewed: `MAX_RISK_PER_TRADE_PCT`, `MAX_OPEN_TRADES`, `DAILY_LOSS_LIMIT_PCT`
- [ ] `MAX_LOT_SIZE` set to a conservative value

### Backup
- [ ] `config.env` backed up to secure location
- [ ] `autotrader.session` backed up
- [ ] Server snapshot taken (if VPS supports it)

---

## 12. Troubleshooting <a name="troubleshooting"></a>

### MT5 Won't Connect

```bash
# Check if Wine is working
sudo -u trader wine --version

# Check if MT5 is installed
ls -la "/home/trader/.wine/drive_c/Program Files/MetaTrader 5/"

# Try running MT5 manually
sudo -u trader bash -c '
    export DISPLAY=:99
    Xvfb :99 -screen 0 1024x768x24 &
    sleep 2
    WINEPREFIX=/home/trader/.wine wine "/home/trader/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"
'
```

### Telegram Session Invalid

```bash
# Delete old session and re-authenticate
rm /home/trader/autotrader/data/autotrader.session
# Run interactively to re-auth
sudo -u trader bash -c 'cd /home/trader/autotrader && source venv/bin/activate && python -m src.main'
```

### AI Parser Not Working

```bash
# Test Gemini API key
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=YOUR_KEY" | head

# Test Groq API key
curl -s -H "Authorization: Bearer YOUR_GROQ_KEY" \
    https://api.groq.com/openai/v1/models | head
```

### ModuleNotFoundError

```bash
# Ensure venv is activated and has all deps
sudo -u trader bash -c '
    cd /home/trader/autotrader
    source venv/bin/activate
    pip install -r requirements.txt
    python -c "import telethon; import groq; import google.generativeai; print(\"All imports OK\")"
'
```

### Port 18812 Not Responding (mt5linux bridge)

```bash
# Check if the bridge is listening
ss -tlnp | grep 18812

# Check mt5-bridge service logs
journalctl -u mt5-bridge -n 50

# Restart the bridge
sudo systemctl restart mt5-bridge
```

### Alpari Symbol Not Found

If `XAUUSD` isn't found, the executor tries suffixes automatically. Check what's available:

```bash
# In the bot logs, look for:
# "Symbol resolved: XAUUSD → XAUUSDm"
journalctl -u autotrader | grep "Symbol resolved"
```

If no suffix works, you may need to manually enable the symbol in MT5 Market Watch (via VNC).
