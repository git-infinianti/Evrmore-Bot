# Evrmore Bot

A production-ready Discord bot for the [Evrmore](https://evrmore.com) blockchain. It runs a custodial wallet inside your Discord server, letting members deposit, withdraw, tip, trade, and mint NFTs with $EVR and Evrmore assets — all through slash commands.

---

## Features

- Custodial $EVR and asset wallets per Discord user
- Deposit addresses with QR codes
- $EVR and asset withdrawals to external addresses
- User-to-user tips and asset transfers
- Server-wide rain and shower (airdrop to all online members)
- On-chain asset search and detail viewer with IPFS image rendering
- `SHOP#` NFT minting at 5 $EVR
- Message signing and signature verification
- Buy order placement and tracking (SQLite)
- Live $EVR price via CoinGecko
- Transaction history viewer
- Admin commands: house wallet balance, bot invite link
- Channel-locked command routing
- Full file-based logging

---

## Requirements

- Python 3.10+
- A running **Evrmore full node** with RPC enabled
- A **Discord bot token** (from the [Discord Developer Portal](https://discord.com/developers/applications))
- Linux (Ubuntu/Debian, Arch, or SteamOS via distrobox)

### Evrmore Node RPC Configuration

In your `evrmore.conf`:

```ini
server=1
rpcuser=your-rpc-username
rpcpassword=your-rpc-password
rpcport=8819
txindex=1
addressindex=1
assetindex=1
timestampindex=1
spentindex=1
zmqpubrawtx=tcp://127.0.0.1:29332
zmqpubhashblock=tcp://127.0.0.1:29332
```

---

## Installation

```bash
git clone https://github.com/your-username/EvrmoreBot.git
cd EvrmoreBot
```

### 1. Configure the bot

Copy the example configuration and edit it:

```bash
cp example-configuration.json configuration.json
```

| Field | Description |
|---|---|
| `default-address` | Your bot's hot wallet EVR address |
| `prefix` | Bot display name (e.g. `"Evrmore Bot"`) |
| `user` | Evrmore RPC username |
| `port` | Evrmore RPC port (default `8819`) |
| `log` | Log file path (e.g. `"evr.log"`) |
| `tx-fee` | Transaction fee in $EVR (default `0.01`) |
| `permissions-integer` | Discord permissions integer |
| `bot-uuids` | Array of bot Discord user IDs |
| `allowed-channel-ids` | Channel IDs where commands are permitted |
| `admin-id` | Discord user ID of the server admin |
| `evr-id` | Discord role or user ID for EVR-related gating |
| `unoff-id` | Unofficial/secondary role or user ID |

### 2. Create the `.env` file

```bash
TOKEN=your_discord_bot_token
PASSWORD=your_evrmore_rpc_password
```

### 3. Run the installer

```bash
chmod +x install.sh
./install.sh
```

The installer auto-detects your OS (Ubuntu/Debian, Arch, SteamOS) and:

- Installs system dependencies
- Creates a Python virtual environment at `~/.Evrmore-Bot/venv/`
- Installs all Python dependencies (including native `coincurve` build)
- Copies bot files to `~/.Evrmore-Bot/`

> **SteamOS note:** The installer runs inside a `distrobox` container to keep the host filesystem untouched. `distrobox` and a container engine (podman or docker) are required.

---

## Usage

```bash
./start.sh   # start the bot in the background
./stop.sh    # stop the running bot
./update.sh  # pull latest changes and restart
./uninstall.sh # remove the bot and all installed files
```

`start.sh` detects distrobox installations automatically and runs the bot inside the container when appropriate.

---

## Commands

### Wallet

| Command | Description |
|---|---|
| `/menu` | Open the interactive control panel |
| `/balance` | Check your $EVR vault balance |
| `/asset` | Check your on-chain asset balances |
| `/deposit` | Get your deposit address and QR code |
| `/withdraw [address] [amount]` | Send $EVR to an external address |
| `/redeem [asset] [address] [amount]` | Send an asset to an external address |
| `/transactions` | View your full transaction history |

### Social

| Command | Description |
|---|---|
| `/tip [user] [amount]` | Slide another member some $EVR |
| `/send [user] [amount] [asset]` | Send an asset to another member |
| `/rain [amount]` | Distribute $EVR equally to all online members |
| `/shower [amount] [asset]` | Distribute an asset equally to all online members |

### Trading

| Command | Description |
|---|---|
| `/buy [asset] [amount] [price]` | Place a buy order (holds $EVR in escrow) |
| `/orders` | View your open and historical buy orders |

### Assets & NFTs

| Command | Description |
|---|---|
| `/nft [asset] [ipfs]` | Mint a `SHOP#` NFT for 5 $EVR |
| `/view [asset]` | View asset details and IPFS artwork |
| `/search [name]` | Search the blockchain for assets by name prefix |

### Crypto Utilities

| Command | Description |
|---|---|
| `/sign [message]` | Sign a message with your deposit address |
| `/verify [signature] [message]` | Verify a signed message |
| `/price` | Get the current $EVR market price |
| `/time` | Get the current UTC time |

### Info

| Command | Description |
|---|---|
| `/help` | List all commands |
| `/info` | Command formats and important disclaimers |

### Admin Only

| Command | Description |
|---|---|
| `/wallet_balance` | Check the house wallet $EVR balance |
| `/invite` | Get the bot's OAuth2 invite link |

---

## Architecture

```
discord_bot.py        — Main bot: commands, RPC calls, embed builders
configuration.json    — Runtime configuration (non-secret values)
.env                  — Secrets: Discord TOKEN, RPC PASSWORD
buy.db                — SQLite database for open buy orders (auto-created)
evr.log               — Rotating log file (path set in configuration.json)
```

The bot uses a single [Evrmore RPC](https://evrmore.com) connection and manages one labeled account per Discord user ID. Assets are tracked by scanning all addresses associated with a user's account.

---

## Security Notes

- **This is a custodial wallet.** The bot's node holds the private keys. Users trust the operator.
- The default transaction fee is `0.01 $EVR`. This covers on-chain fees and is collected by the house account.
- Deposits are intended for active use — not long-term storage (recommended range: 1–100,000 $EVR).
- Commands are restricted to explicitly configured channel IDs. Attempts in other channels are silently rejected with an ephemeral response.
- The RPC password is loaded exclusively from the `.env` file and never logged or embedded in code.
- Always triple-check destination addresses before withdrawing. Transactions are irreversible.

**Use at your own risk.**

---

## Dependencies

| Package | Purpose |
|---|---|
| `discord.py` | Discord API |
| `python-dotenv` | `.env` secret loading |
| `requests` | HTTP (RPC + CoinGecko price feed) |
| `evrmore-rpc` | Evrmore node RPC helpers |
| `hdwallet` | HD wallet utilities |
| `coincurve` | Elliptic curve cryptography (native build) |
| `aiohttp` | Async HTTP |
| `beautifulsoup4` | HTML parsing |

---

## License

See [LICENSE](LICENSE).
