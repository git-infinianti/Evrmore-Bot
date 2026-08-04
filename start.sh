#!/bin/bash

# Use home directory installation path for Steam OS compatibility
BOT_DIR="${HOME}/.local/share/evrNFTBOT"
VENV="$BOT_DIR/venv"
BOT_SCRIPT="$BOT_DIR/discord_bot.py"
LOG_FILE="$BOT_DIR/bot.log"
PID_FILE="$BOT_DIR/bot.pid"
DISTROBOX_MARKER="$BOT_DIR/.distrobox_container"

echo "Starting Evrmore NFT Bot..."

# Check if bot directory exists
if [ ! -d "$BOT_DIR" ]; then
    echo "❌ Error: Bot directory not found at $BOT_DIR"
    echo "Please run install.sh first"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "$VENV" ]; then
    echo "❌ Error: Virtual environment not found at $VENV"
    echo "Please run install.sh first"
    exit 1
fi

# Check if bot script exists
if [ ! -f "$BOT_SCRIPT" ]; then
    echo "❌ Error: Bot script not found at $BOT_SCRIPT"
    exit 1
fi

# Check if configuration.json exists
if [ ! -f "$BOT_DIR/configuration.json" ]; then
    echo "❌ Error: configuration.json not found at $BOT_DIR/configuration.json"
    echo "Please configure the bot first"
    exit 1
fi

# Check if bot is already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠ Bot is already running (PID: $OLD_PID)"
        echo "To stop it, run: ./stop.sh"
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# Start the bot in the background with nohup
if [ -f "$DISTROBOX_MARKER" ]; then
    CONTAINER_NAME=$(cat "$DISTROBOX_MARKER")
    if ! command -v distrobox &> /dev/null; then
        echo "❌ Error: this bot was installed via distrobox (container: $CONTAINER_NAME) but distrobox is not available"
        exit 1
    fi
    echo "Starting bot process inside distrobox container '$CONTAINER_NAME'..."
    nohup distrobox enter "$CONTAINER_NAME" -- bash -c "cd '$BOT_DIR' && exec '$VENV/bin/python' '$BOT_SCRIPT'" >> "$LOG_FILE" 2>&1 &
    disown 2>/dev/null || true
    sleep 3
    # distrobox shares the host PID namespace, so the containerized process is
    # visible (and killable) from the host; find its real PID rather than $!,
    # which would only be the "distrobox enter" wrapper's PID.
    BOT_PID=$(pgrep -f "$VENV/bin/python $BOT_SCRIPT" | head -n1)
else
    echo "Starting bot process..."
    cd "$BOT_DIR"
    nohup "$VENV/bin/python" "$BOT_SCRIPT" >> "$LOG_FILE" 2>&1 &
    BOT_PID=$!
    sleep 2
fi

if [ -z "$BOT_PID" ]; then
    echo "❌ Failed to start bot. Check the log file:"
    echo "  tail -f $LOG_FILE"
    exit 1
fi

# Save PID to file
echo "$BOT_PID" > "$PID_FILE"

if ps -p "$BOT_PID" > /dev/null 2>&1; then
    echo "✓ Bot started successfully (PID: $BOT_PID)"
    echo ""
    echo "Bot details:"
    echo "  Directory: $BOT_DIR"
    echo "  Process ID: $BOT_PID"
    echo "  Log file: $LOG_FILE"
    echo ""
    echo "To view logs in real-time:"
    echo "  tail -f $LOG_FILE"
    echo ""
    echo "To stop the bot:"
    echo "  ./stop.sh"
else
    echo "❌ Failed to start bot. Check the log file:"
    echo "  tail -f $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
