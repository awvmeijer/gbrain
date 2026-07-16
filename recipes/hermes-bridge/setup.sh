#!/bin/bash
# Hermes ⇄ brain bridge setup — run INTERACTIVELY (reads the Keychain — never
# from launchd; see the -25320 lock trap). Idempotent; merges surgically into
# the existing ~/.hermes/config.yaml (never replaces it).
#
# What it does:
#   1. model → local Ollama qwen3:30b-a3b (free, tool-capable loop) + aliases
#      (`hermes model qwen-local` / `hermes model bridge`).
#   2. mcp_servers.brain → the stdio shim (sidecars/hermes-shim/brain_mcp.py)
#      with a hard tools.include whitelist (search/ask/capture/needs/propose).
#   3. Telegram gateway env (TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USERS) in
#      ~/.hermes/.env from Keychain.
#   4. SOUL.md ← persona.md (existing SOUL.md backed up).
#   5. ~/.gbrain/telegram_approvals.env for the approvals bot (0600).
#
# Prereqs (one-time):
#   * Hermes installed (already present: `hermes --version`).
#   * Two bots via @BotFather; your numeric id via @userinfobot.
#   * security add-generic-password -U -s brain -a TELEGRAM_HERMES_BOT_TOKEN    -w '<token1>'
#     security add-generic-password -U -s brain -a TELEGRAM_APPROVALS_BOT_TOKEN -w '<token2>'
#     security add-generic-password -U -s brain -a TELEGRAM_ALLOWED_USER_ID     -w '<numeric id>'
#
# After: `hermes gateway install` (Hermes's own launchd service) + load
# infra/launchd/com.brains.telegram-approvals.plist.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HERMES_DIR="$HOME/.hermes"
GB="$HOME/.gbrain"
PY="$HOME/gbrain/sidecars/.venv/bin/python"

kc() { security find-generic-password -s brain -a "$1" -w 2>/dev/null || true; }

HERMES_TOKEN="$(kc TELEGRAM_HERMES_BOT_TOKEN)"
APPROVALS_TOKEN="$(kc TELEGRAM_APPROVALS_BOT_TOKEN)"
USER_ID="$(kc TELEGRAM_ALLOWED_USER_ID)"

[ -n "$HERMES_TOKEN" ]    || { echo "missing Keychain item TELEGRAM_HERMES_BOT_TOKEN (see header)"; exit 1; }
[ -n "$APPROVALS_TOKEN" ] || { echo "missing Keychain item TELEGRAM_APPROVALS_BOT_TOKEN"; exit 1; }
[ -n "$USER_ID" ]         || { echo "missing Keychain item TELEGRAM_ALLOWED_USER_ID"; exit 1; }
[ -f "$HERMES_DIR/config.yaml" ] || { echo "no ~/.hermes/config.yaml — install/run Hermes once first"; exit 1; }

# --- 1+2. Surgical config.yaml merge (backup first) ---------------------------
cp "$HERMES_DIR/config.yaml" "$HERMES_DIR/config.yaml.bak.$(date +%s)"
"$PY" - "$HERMES_DIR/config.yaml" <<'PYEOF'
import sys, yaml

path = sys.argv[1]
cfg = yaml.safe_load(open(path)) or {}

# Model: local Ollama drives the tool-calling loop ($0). Deep synthesis flows
# through the brain_ask MCP tool -> gbrain think -> Claude Max bridge.
model = cfg.setdefault("model", {})
model["default"] = "qwen3:30b-a3b"
model["provider"] = "ollama"          # alias resolving to `custom` (loopback trusted)
model["base_url"] = "http://127.0.0.1:11434/v1"

# Named aliases for quick switching: `hermes model qwen-local` / `hermes model bridge`
aliases = cfg.setdefault("model_aliases", {})
aliases["qwen-local"] = {"model": "qwen3:30b-a3b", "provider": "custom",
                         "base_url": "http://127.0.0.1:11434/v1"}
aliases["bridge"] = {"model": "claude-sonnet", "provider": "custom",
                     "base_url": "http://127.0.0.1:8789/v1"}  # text-only (no tool calls)

# The brain, via the stdio shim. tools.include is safety layer 2 (layer 1 is
# the shim's tiny surface): no decide, no raw writes, no send — propose only.
home = "/Users/awvmeijer"
cfg.setdefault("mcp_servers", {})["brain"] = {
    "command": f"{home}/gbrain/sidecars/.venv/bin/python",
    "args": [f"{home}/gbrain/sidecars/hermes-shim/brain_mcp.py"],
    "timeout": 300,  # brain_ask runs gbrain think (Claude synthesis) — slow is normal
    "tools": {"include": ["brain_search", "brain_ask", "brain_capture",
                           "brain_needs", "brain_propose"],
              "resources": False, "prompts": False},
}

yaml.safe_dump(cfg, open(path, "w"), sort_keys=False, allow_unicode=True, width=100)
print("✓ config.yaml merged (model → ollama qwen3:30b-a3b, mcp_servers.brain added)")
PYEOF

# --- 3. Telegram gateway env --------------------------------------------------
touch "$HERMES_DIR/.env"; chmod 600 "$HERMES_DIR/.env"
tmp="$(mktemp)"
grep -v '^TELEGRAM_BOT_TOKEN=\|^TELEGRAM_ALLOWED_USERS=' "$HERMES_DIR/.env" > "$tmp" || true
{
  echo "TELEGRAM_BOT_TOKEN=$HERMES_TOKEN"
  echo "TELEGRAM_ALLOWED_USERS=$USER_ID"
} >> "$tmp"
mv "$tmp" "$HERMES_DIR/.env"; chmod 600 "$HERMES_DIR/.env"
echo "✓ .env: TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USERS=$USER_ID"

# --- 4. Persona ----------------------------------------------------------------
[ -f "$HERMES_DIR/SOUL.md" ] && cp "$HERMES_DIR/SOUL.md" "$HERMES_DIR/SOUL.md.bak.$(date +%s)"
cp "$HERE/persona.md" "$HERMES_DIR/SOUL.md"
echo "✓ SOUL.md ← persona.md"

# --- 5. Approvals bot secrets (0600; launchd-safe file, not Keychain) ----------
umask 077
cat > "$GB/telegram_approvals.env" <<EOF
TELEGRAM_APPROVALS_BOT_TOKEN=$APPROVALS_TOKEN
TELEGRAM_ALLOWED_USER_ID=$USER_ID
EOF
echo "✓ ~/.gbrain/telegram_approvals.env"

echo
echo "Next:"
echo "  hermes model                 # confirm qwen3:30b-a3b (custom/ollama) is active"
echo "  hermes                       # smoke test: 'search my brain for FCEL'"
echo "  hermes gateway install && hermes gateway start   # Telegram, as a native launchd service"
echo "  cp ~/gbrain/infra/launchd/com.brains.telegram-approvals.plist ~/Library/LaunchAgents/"
echo "  launchctl load ~/Library/LaunchAgents/com.brains.telegram-approvals.plist"
