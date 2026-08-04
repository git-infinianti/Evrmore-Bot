#!/bin/bash

# Use home directory installation path for Steam OS compatibility
BOT_DIR="${HOME}/.local/share/evrNFTBOT"
PID_FILE="$BOT_DIR/bot.pid"
LOG_FILE="$BOT_DIR/bot.log"

echo "Stopping Evrmore NFT Bot..."

# Check if PID file exists
if [ ! -f "$PID_FILE" ]; then
    echo "⚠ No PID file found. Bot may not be running."
    exit 0
fi

# Read the PID
BOT_PID=$(cat "$PID_FILE")

# Check if process exists
if ! ps -p "$BOT_PID" > /dev/null 2>&1; then
    echo "⚠ Bot process (PID: $BOT_PID) is not running"
    rm -f "$PID_FILE"
    exit 0
fi

echo "Found bot process (PID: $BOT_PID)"
echo "Sending termination signal..."

# Gracefully terminate the bot
kill -TERM "$BOT_PID" 2>/dev/null

# Wait for process to terminate (up to 10 seconds)
COUNTER=0
while ps -p "$BOT_PID" > /dev/null 2>&1 && [ $COUNTER -lt 10 ]; do
    sleep 1
    COUNTER=$((COUNTER + 1))
done

# Force kill if still running
if ps -p "$BOT_PID" > /dev/null 2>&1; then
    echo "Process did not terminate gracefully, force killing..."
    kill -9 "$BOT_PID" 2>/dev/null
    sleep 1
fi

# Check if process is actually stopped
if ps -p "$BOT_PID" > /dev/null 2>&1; then
    echo "❌ Failed to stop bot (PID: $BOT_PID)"
    exit 1
else
    echo "✓ Bot stopped successfully"
    rm -f "$PID_FILE"
fi

# Show last lines of log
echo ""
echo "Last log entries:"
echo "================================"
tail -n 5 "$LOG_FILE" 2>/dev/null || echo "(No log file found)"
echo "================================"
echo ""
echo "To view the full log:"
echo "  tail -f $LOG_FILE"
echo ""
echo "To start the bot again:"
echo "  ./start.sh"
