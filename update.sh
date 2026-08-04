#!/bin/bash

set -e

echo "Updating Evrmore NFT Bot..."

# Resolve the directory this script lives in, so source files are found
# regardless of $HOME (e.g. when run via sudo).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use home directory installation path for Steam OS compatibility
INSTALL_USER="${SUDO_USER:-$USER}"
USER_HOME="$(eval echo "~${INSTALL_USER}")"
BOT_INSTALL_DIR="${USER_HOME}/.local/share/evrNFTBOT"
VENV="$BOT_INSTALL_DIR/venv"
DISTROBOX_MARKER="$BOT_INSTALL_DIR/.distrobox_container"
PID_FILE="$BOT_INSTALL_DIR/bot.pid"

if [ ! -d "$BOT_INSTALL_DIR" ] || [ ! -d "$VENV" ]; then
    echo "❌ Error: Bot is not installed at $BOT_INSTALL_DIR"
    echo "Please run install.sh first"
    exit 1
fi

# Stop the bot first so its files aren't swapped out from under a running process
WAS_RUNNING=0
if [ -f "$PID_FILE" ] && ps -p "$(cat "$PID_FILE")" > /dev/null 2>&1; then
    WAS_RUNNING=1
    echo "Stopping running bot before updating..."
    "$SCRIPT_DIR/stop.sh"
fi

echo "Copying updated bot files..."
cp -prf "$SCRIPT_DIR/discord_bot.py" "$BOT_INSTALL_DIR/discord_bot.py"
cp -prf "$SCRIPT_DIR/channels.json" "$BOT_INSTALL_DIR/channels.json"

# Never clobber a live configuration.json (it holds RPC creds/token) — only
# drop one in if the install is somehow missing it.
if [ ! -f "$BOT_INSTALL_DIR/configuration.json" ]; then
    cp -prf "$SCRIPT_DIR/configuration.json" "$BOT_INSTALL_DIR/configuration.json"
fi

# Never clobber a live .env (it holds the bot TOKEN and RPC PASSWORD) — only
# drop one in if the install is somehow missing it.
if [ ! -f "$BOT_INSTALL_DIR/.env" ]; then
    cp -prf "$SCRIPT_DIR/.env" "$BOT_INSTALL_DIR/.env"
fi

echo "Updating Python dependencies..."
if [ -f "$DISTROBOX_MARKER" ]; then
    CONTAINER_NAME=$(cat "$DISTROBOX_MARKER")
    if ! command -v distrobox &> /dev/null; then
        echo "❌ Error: this bot was installed via distrobox (container: $CONTAINER_NAME) but distrobox is not available"
        exit 1
    fi
    distrobox enter "$CONTAINER_NAME" -- bash -c "
        set -e
        source '$VENV/bin/activate'
        pip install --upgrade pip
        pip install --upgrade discord.py aiohttp beautifulsoup4 requests hdwallet evrmore-rpc python-dotenv
    "
else
    source "$VENV/bin/activate"
    pip install --upgrade pip
    pip install --upgrade discord.py aiohttp beautifulsoup4 requests hdwallet evrmore-rpc python-dotenv
    deactivate
fi

echo ""
echo "============================================"
echo "Update complete!"
echo "============================================"
echo ""

if [ "$WAS_RUNNING" -eq 1 ]; then
    echo "Restarting bot..."
    "$SCRIPT_DIR/start.sh"
else
    echo "Bot files are up to date. Start it with:"
    echo "  ./start.sh"
fi
