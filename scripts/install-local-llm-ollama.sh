#!/usr/bin/env bash
set -euo pipefail

# Installs Ollama, pulls Gemma 3 1B, creates a local alias expected by AutoTrader,
# and updates config.env with Ollama primary parser settings.

PROJECT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONFIG_FILE="$PROJECT_DIR/config.env"
MODEL_SOURCE="gemma3:1b"
MODEL_ALIAS="gemma3:1b-q4_K_M"

log() {
  printf "[ollama-setup] %s\n" "$*"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    log "Please run as root: sudo bash $0"
    exit 1
  fi
}

install_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    log "Ollama already installed: $(ollama --version 2>/dev/null || echo unknown version)"
    return
  fi

  log "Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
}

start_ollama_service() {
  log "Ensuring Ollama service is enabled and running..."
  systemctl enable --now ollama
  systemctl is-active --quiet ollama
}

pull_and_alias_model() {
  log "Pulling model: $MODEL_SOURCE"
  ollama pull "$MODEL_SOURCE"

  # Create the alias expected by AutoTrader.
  log "Creating local alias: $MODEL_ALIAS"
  ollama cp "$MODEL_SOURCE" "$MODEL_ALIAS" || true

  log "Installed models:"
  ollama list
}

upsert_env_key() {
  local key="$1"
  local value="$2"

  if grep -qE "^${key}=" "$CONFIG_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$CONFIG_FILE"
  else
    printf "\n%s=%s\n" "$key" "$value" >> "$CONFIG_FILE"
  fi
}

configure_env() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    log "No config.env found at $CONFIG_FILE (skipping env update)."
    return
  fi

  log "Updating config.env for Ollama primary parser..."
  upsert_env_key "OLLAMA_ENABLED" "true"
  upsert_env_key "OLLAMA_BASE_URL" "http://127.0.0.1:11434"
  upsert_env_key "OLLAMA_MODEL" "$MODEL_ALIAS"
  upsert_env_key "OLLAMA_MODEL_RATE_LIMITS" "${MODEL_ALIAS}=10/5000"

  log "config.env updated."
}

verify_ollama_chat() {
  log "Running a quick local model check..."
  ollama run "$MODEL_ALIAS" "Respond with exactly: {\"is_signal\": false}" >/dev/null
  log "Local model check passed."
}

main() {
  require_root
  install_ollama
  start_ollama_service
  pull_and_alias_model
  configure_env
  verify_ollama_chat

  log "Done. You can now run:"
  log "  cd $PROJECT_DIR/src && ../venv/bin/python ai_parser.py --text \"BUY XAUUSD 2340 SL 2332 TP1 2350\""
}

main "$@"
