#!/bin/bash

set -e

echo "Installing Evrmore Bot with Python virtual environment..."

# Detect OS and set package manager
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    echo "❌ Error: Cannot detect operating system"
    exit 1
fi

echo "Detected OS: $OS"

# Resolve the directory this script lives in, so source files are found
# regardless of $HOME (e.g. when the whole script is run via sudo, which
# resets $HOME to /root and breaks a ~/evrNFTBot-based path).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create bot directory (use the invoking user's home directory, not root's,
# even if this script itself is run with sudo)
INSTALL_USER="${SUDO_USER:-$USER}"
USER_HOME="$(eval echo "~${INSTALL_USER}")"
BOT_INSTALL_DIR="${USER_HOME}/.Evrmore-Bot"

# SteamOS's root filesystem is read-only and its signing-key setup is
# unreliable for direct pacman installs, so install into a distrobox
# container instead of touching the host system at all.
install_steamos_distrobox() {
    echo "Using distrobox to install on SteamOS (host filesystem stays untouched)..."

    # distrobox refuses to run under sudo/root entirely (it has no --root
    # support for `create`/`enter` in that mode). Since nothing here touches
    # the read-only host filesystem, drop back to the invoking user instead.
    if [[ "$EUID" -eq 0 ]]; then
        echo "Re-running as $INSTALL_USER (distrobox cannot run as root)..."
        exec sudo -u "$INSTALL_USER" -H "$SCRIPT_DIR/install.sh"
    fi

    if ! command -v distrobox &> /dev/null; then
        echo "❌ Error: distrobox was not found. SteamOS ships distrobox by default;"
        echo "   if it's missing, see https://github.com/89luca89/distrobox for install instructions."
        exit 1
    fi

    if ! command -v podman &> /dev/null && ! command -v docker &> /dev/null; then
        echo "❌ Error: distrobox requires a container engine (podman or docker), and neither was found."
        exit 1
    fi

    CONTAINER_NAME="evrbot"

    if distrobox list --no-color 2>/dev/null | awk -F'|' '{print $2}' | tr -d ' ' | grep -qx "$CONTAINER_NAME"; then
        echo "Distrobox container '$CONTAINER_NAME' already exists, reusing it..."
    else
        echo "Creating distrobox container '$CONTAINER_NAME' (Arch Linux)..."
        distrobox create --name "$CONTAINER_NAME" --image archlinux:latest --yes
    fi

    echo "Installing python and build tools inside the container..."
    distrobox enter "$CONTAINER_NAME" -- sudo pacman -Syu --noconfirm --needed python base-devel

    # A prior run that happened to execute as root (or a botched venv build)
    # can leave files under $BOT_INSTALL_DIR owned by a different user, which
    # then causes "Permission denied" here even though this script is now
    # running as $INSTALL_USER.
    if [ -d "$BOT_INSTALL_DIR" ] && [ "$(stat -c '%U' "$BOT_INSTALL_DIR")" != "$INSTALL_USER" ]; then
        echo "Fixing ownership of $BOT_INSTALL_DIR..."
        sudo chown -R "$INSTALL_USER:$INSTALL_USER" "$BOT_INSTALL_DIR"
    fi

    mkdir -p "$BOT_INSTALL_DIR"
    echo "Copying bot files..."
    cp -prf "$SCRIPT_DIR/discord_bot.py" "$BOT_INSTALL_DIR/discord_bot.py"
    cp -prf "$SCRIPT_DIR/configuration.json" "$BOT_INSTALL_DIR/configuration.json"
    cp -prf "$SCRIPT_DIR/channels.json" "$BOT_INSTALL_DIR/channels.json"
    cp -prf "$SCRIPT_DIR/.env" "$BOT_INSTALL_DIR/.env"

    echo "Creating Python virtual environment inside the container..."
    # Remove any partial/stale venv left from a previous failed attempt so
    # venv creation doesn't choke on leftover files it can't overwrite.
    rm -rf "$BOT_INSTALL_DIR/venv"
    distrobox enter "$CONTAINER_NAME" -- python3 -m venv "$BOT_INSTALL_DIR/venv"

    echo "Installing Python dependencies inside the container..."
    distrobox enter "$CONTAINER_NAME" -- bash -c "
        set -e
        source '$BOT_INSTALL_DIR/venv/bin/activate'
        pip install --upgrade pip
        pip install 'scikit-build-core[pyproject]<0.10' cmake ninja hatchling cffi
        pip install --no-build-isolation 'coincurve>=20.0.0,<21'
        pip install discord.py aiohttp beautifulsoup4 requests hdwallet evrmore-rpc python-dotenv
    "

    # Record which container this install lives in so start.sh/stop.sh know how to run it
    echo "$CONTAINER_NAME" > "$BOT_INSTALL_DIR/.distrobox_container"

    echo ""
    echo "============================================"
    echo "Installation complete (via distrobox)!"
    echo "============================================"
    echo ""
    echo "Bot files installed to: $BOT_INSTALL_DIR"
    echo "Distrobox container: $CONTAINER_NAME"
    echo ""
    echo "To run the bot:"
    echo "  1. Update configuration.json with your credentials:"
    echo "     nano $BOT_INSTALL_DIR/configuration.json"
    echo "  2. Update .env with your bot TOKEN and RPC PASSWORD:"
    echo "     nano $BOT_INSTALL_DIR/.env"
    echo "  3. Update channels.json with the channels I should listen for commands in:"
    echo "     nano $BOT_INSTALL_DIR/channels.json"
    echo ""
    echo "  4. Start the bot:"
    echo "     ./start.sh"
    echo ""
    echo "start.sh/stop.sh detect the container automatically and run the bot inside it."
}

# Update system packages based on OS
if [[ "$OS" == "steamos" ]]; then
    install_steamos_distrobox
    exit 0
elif [[ "$OS" == "arch" ]]; then
    echo "Using pacman for package management (Arch)..."
    echo "Initializing pacman keyring..."
    sudo pacman-key --init
    sudo pacman-key --populate archlinux
    echo "Updating package keyrings..."
    sudo pacman -Sy --noconfirm --needed archlinux-keyring
    sudo pacman -Syu --noconfirm --needed python base-devel
elif [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
    echo "Using apt for package management (Debian/Ubuntu)..."
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip
else
    echo "⚠ Warning: Unsupported OS detected ($OS). Attempting generic installation..."
    if command -v pacman &> /dev/null; then
        sudo pacman -Syu --noconfirm python base-devel
    elif command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-venv python3-pip
    else
        echo "❌ Error: No supported package manager found"
        exit 1
    fi
fi

# base-devel/build-essential installs above are allowed to fail (e.g. SteamOS
# signature-verification issues), which can silently leave gcc missing. Some
# Python deps (e.g. coincurve) have no prebuilt wheel for newer Python
# versions and must compile from source, so verify a compiler actually made
# it onto the system before we get to pip, where the failure is much harder
# to diagnose (a CMake error buried in a pip build log).
if ! command -v gcc &> /dev/null && ! command -v cc &> /dev/null; then
    echo "⚠ No C compiler found; retrying build tools installation..."
    if command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm --needed gcc make pkgconf || true
    elif command -v apt-get &> /dev/null; then
        sudo apt-get install -y build-essential || true
    fi
fi

if ! command -v gcc &> /dev/null && ! command -v cc &> /dev/null; then
    echo "❌ Error: No C compiler (gcc/cc) is available."
    echo "   Some Python dependencies (e.g. coincurve) have no prebuilt wheel for"
    echo "   this Python version and must be compiled from source, which requires gcc."
    echo "   Install a C compiler manually and re-run this script, e.g.:"
    echo "     sudo pacman -S --needed base-devel      # Arch"
    echo "     sudo apt-get install -y build-essential  # Debian/Ubuntu"
    exit 1
fi

if [ -d "$BOT_INSTALL_DIR" ] && [ "$(stat -c '%U' "$BOT_INSTALL_DIR")" != "$INSTALL_USER" ]; then
    echo "Fixing ownership of $BOT_INSTALL_DIR..."
    sudo chown -R "$INSTALL_USER:$INSTALL_USER" "$BOT_INSTALL_DIR"
fi

mkdir -p "$BOT_INSTALL_DIR"
cd "$BOT_INSTALL_DIR"

# Create Python virtual environment
echo "Creating Python virtual environment..."
# Remove any partial/stale venv left from a previous failed attempt so
# venv creation doesn't choke on leftover files it can't overwrite.
rm -rf venv
python3 -m venv venv

# Copy bot files
echo "Copying bot files..."
cp -prf "$SCRIPT_DIR/discord_bot.py" "$BOT_INSTALL_DIR/discord_bot.py"
cp -prf "$SCRIPT_DIR/configuration.json" "$BOT_INSTALL_DIR/configuration.json"
cp -prf "$SCRIPT_DIR/channels.json" "$BOT_INSTALL_DIR/channels.json"
cp -prf "$SCRIPT_DIR/.env" "$BOT_INSTALL_DIR/.env"

# Permissions are already correct since we're in home directory

# Activate virtual environment and install dependencies
echo "Installing Python dependencies..."
source "$BOT_INSTALL_DIR/venv/bin/activate"
pip install --upgrade pip

# hdwallet pins coincurve<21, whose sdist pyproject.toml uses a scikit-build-core
# option name removed in scikit-build-core>=0.10. On systems without a prebuilt
# coincurve wheel (e.g. newer Python on SteamOS), pip builds from source and its
# isolated build env fetches the latest (incompatible) scikit-build-core, which
# fails with "Use build.verbose instead of cmake.verbose". Pre-install a
# compatible scikit-build-core and build coincurve without isolation to avoid it.
# coincurve's build-backend is hatchling.build (with a scikit-build-core hatch
# hook for the CMake step), so hatchling and cffi must also be pre-installed
# or --no-build-isolation fails with "Cannot import 'hatchling.build'".
echo "Pre-installing build tools for native dependencies..."
pip install "scikit-build-core[pyproject]<0.10" cmake ninja hatchling cffi
pip install --no-build-isolation "coincurve>=20.0.0,<21"

pip install discord.py aiohttp beautifulsoup4 requests hdwallet evrmore-rpc python-dotenv

echo ""
echo "============================================"
echo "Installation complete!"
echo "============================================"
echo ""
echo "Bot files installed to: $BOT_INSTALL_DIR"
echo "Python virtual environment: $BOT_INSTALL_DIR/venv/"
echo ""
echo "To run the bot:"
echo "  1. Update configuration.json with your credentials:"
echo "     nano $BOT_INSTALL_DIR/configuration.json"
echo "  2. Update .env with your bot TOKEN and RPC PASSWORD:"
echo "     nano $BOT_INSTALL_DIR/.env"
echo "  3. Update channels.json with the channels I should listen for commands in:"
echo "     nano $BOT_INSTALL_DIR/channels.json"
echo ""
echo "  4. Start the bot:"
echo "     cd $BOT_INSTALL_DIR"
echo "     source venv/bin/activate"
echo "     python discord_bot.py"
echo ""
echo "To run in background using screen:"
echo "     screen -S evr-bot"
echo "     cd $BOT_INSTALL_DIR && source venv/bin/activate && python discord_bot.py"
echo ""
echo "Or use the start.sh script:"
echo "     ./start.sh"
echo "     Press Ctrl+A then D to detach"
echo ""
