# Evrmore Bot Configuration Guide

## Public RPC Endpoints

The bot now supports public RPC API servers provided by the Evrmore community:

- **Testnet**: `https://evr-rpc-testnet.evrmorecoin.org` (port 443)
- **Mainnet**: `https://evr-rpc-mainnet.evrmorecoin.org` (port 443)

## Configuration Options

### Option 1: Using Public RPC (Recommended for Testing)

Edit `configuration.json`:

```json
{
    "host": "https://evr-rpc-testnet.evrmorecoin.org",
    "port": 443,
    "network": "testnet",
    "user": "evrmoreuser",
    "password": "your-rpc-password"
}
```

For mainnet:

```json
{
    "host": "https://evr-rpc-mainnet.evrmorecoin.org",
    "port": 443,
    "network": "mainnet",
    "user": "evrmoreuser",
    "password": "your-rpc-password"
}
```

### Option 2: Using Local Node

Edit `configuration.json`:

```json
{
    "host": "localhost",
    "port": 8819,
    "network": "mainnet",
    "user": "your-rpc-username",
    "password": "your-rpc-password"
}
```

For testnet with local node:

```json
{
    "host": "localhost",
    "port": 8766,
    "network": "testnet",
    "user": "your-rpc-username",
    "password": "your-rpc-password"
}
```

### Option 3: Environment Variables

You can override configuration using environment variables:

```bash
export RPC_HOST="https://evr-rpc-testnet.evrmorecoin.org"
export RPC_PORT="443"
export RPC_USER="evrmoreuser"
export PASSWORD="your-rpc-password"
```

## Network Settings

The `network` field in `configuration.json` determines address derivation:

- `"testnet"`: Addresses start with `m` or `n`
- `"mainnet"`: Addresses start with `E`

**Important**: Make sure the network setting matches your RPC endpoint!

## Required Fields

| Field | Description | Default |
|-------|-------------|---------|
| `host` | RPC server URL or hostname | `localhost` |
| `port` | RPC server port | `8819` (mainnet), `8766` (testnet) |
| `network` | Network type | `testnet` |
| `user` | RPC username | `evrmoreuser` |
| `password` | RPC password | (required) |
| `admin-id` | Your Discord user ID | (required for admin commands) |

## Getting RPC Credentials

### For Public RPC
Contact the Evrmore community to obtain RPC credentials for the public endpoints.

### For Local Node
In your `evrmore.conf` file:

```
rpcuser=your-username
rpcpassword=your-secure-password
server=1
txindex=1
addressindex=1
spentindex=1
```

For testnet, also add:
```
testnet=1
```

## Quick Start

1. Edit `configuration.json` with your preferred settings
2. Set environment variables (optional):
   ```bash
   export TOKEN="your-discord-bot-token"
   export PASSWORD="your-rpc-password"
   ```
3. Run the bot:
   ```bash
   python3 discord_bot_refactored.py
   ```

## Switching Between Networks

To switch from testnet to mainnet:

1. Change `network` to `"mainnet"`
2. Change `host` to `"https://evr-rpc-mainnet.evrmorecoin.org"` or your mainnet node
3. Change `port` to `443` (public RPC) or `8819` (local)
4. Restart the bot

**Warning**: Changing networks will use different HD wallet derivation paths. Existing addresses will not work on the other network.
