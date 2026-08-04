#!/bin/bash

set -e

echo "Uninstalling Evrmore NFT Bot..."

# Use home directory installation path for Steam OS compatibility
BOT_DIR="${HOME}/.local/share/evrNFTBOT"

# Read the distrobox container name (if any) before the bot directory is removed
DISTROBOX_MARKER="$BOT_DIR/.distrobox_container"
CONTAINER_NAME=""
if [ -f "$DISTROBOX_MARKER" ]; then
    CONTAINER_NAME=$(cat "$DISTROBOX_MARKER")
fi

# Remove bot installation directory
if [ -d "$BOT_DIR" ]; then
    rm -rf "$BOT_DIR"
    echo "✓ Removed bot files from $BOT_DIR"
else
    echo "⚠ Bot directory not found at $BOT_DIR"
fi

# Remove the distrobox container this bot was installed into, if any
if [ -n "$CONTAINER_NAME" ]; then
    if command -v distrobox &> /dev/null; then
        distrobox rm --force "$CONTAINER_NAME" 2>/dev/null && echo "✓ Removed distrobox container '$CONTAINER_NAME'" || echo "⚠ Failed to remove distrobox container '$CONTAINER_NAME'"
    else
        echo "⚠ distrobox not found; skipping removal of container '$CONTAINER_NAME'"
    fi
fi

# Remove log files if they exist
if [ -f "$BOT_DIR/bot.log" ]; then
    rm -f "$BOT_DIR/bot.log"
    echo "✓ Removed bot log file"
fi

if [ -f "./evr.log" ]; then
    rm -f ./evr.log
    echo "✓ Removed local log file"
fi

# Optional: Remove systemd service if it exists (requires sudo)
if [ -f "/etc/systemd/system/evr-nft-bot.service" ]; then
    sudo systemctl stop evr-nft-bot.service 2>/dev/null || true
    sudo systemctl disable evr-nft-bot.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/evr-nft-bot.service
    sudo systemctl daemon-reload 2>/dev/null || true
    echo "✓ Removed systemd service"
fi

echo "Uninstallation complete!"
echo "Bot files and configurations have been removed."
