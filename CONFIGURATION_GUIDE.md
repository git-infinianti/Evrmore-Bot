# DeFi-Tome Inspired Discord Bot - Configuration Guide

## Overview
This bot implements DeFi-Tome design principles with:
- **HD Wallet** with BIP39/BIP44 derivation
- **Secure SQLite Storage** for entropy (private keys never stored directly)
- **Raw Transaction Workflow** (no reliance on node wallet management)
- **Configurable RPC Endpoint** with testnet by default

## Configuration Files

### 1. `.env` File (Optional)
Set environment variables for sensitive credentials:

```bash
TOKEN=your_discord_bot_token
PASSWORD=your_rpc_password
RPC_USER=your_rpc_username
RPC_HOST=localhost
RPC_PORT=8766
```

**Note:** If TOKEN is not set, the bot will display startup instructions and exit gracefully.

### 2. `configuration.json` (Required)
Main configuration file with the following structure:

```json
{
    "default-address": "YOUR_EVR_WALLET_ADDRESS",
    "prefix": "Evrmore Bot",
    "user": "your-rpc-username",
    "password": "your-rpc-password",
    "host": "localhost",
    "port": 8819,
    "network": "testnet",
    "log": "evr.log",
    "tx-fee": 0.01,
    "format": "%(asctime)-15s - %(message)s",
    "permissions-integer": 4503599627503680,
    "bot-uuids": [123456789012345678],
    "allowed-channel-ids": [123456789012345678],
    "admin-id": 123456789012345678,
    "evr-id": 123456789012345678,
    "unoff-id": 123456789012345679
}
```

### Key Configuration Options

#### Network Setting (NEW)
- **Default:** `"testnet"`
- **Options:** `"testnet"` or `"mainnet"`
- **Description:** Determines which network the HD wallet derives addresses for

```json
"network": "testnet"
```

To switch to mainnet:
```json
"network": "mainnet"
```

#### RPC Host (NEW)
- **Default:** `"localhost"`
- **Description:** RPC server hostname or IP address

```json
"host": "localhost"
```

For remote RPC:
```json
"host": "192.168.1.100"
```

#### RPC Port
- **Testnet Default:** `8766`
- **Mainnet Default:** `8819`
- **Description:** RPC server port

```json
"port": 8766
```

#### RPC Credentials
```json
"user": "your-rpc-username",
"password": "your-rpc-password"
```

**Security Note:** Password can be stored in `.env` file instead of `configuration.json` for better security.

## Running the Bot

### Option 1: Using Environment Variables
```bash
export TOKEN="your_discord_bot_token"
export PASSWORD="your_rpc_password"
python3 discord_bot_refactored.py
```

### Option 2: Using .env File
Create a `.env` file with your credentials:
```bash
TOKEN=your_discord_bot_token
PASSWORD=your_rpc_password
```

Then run:
```bash
python3 discord_bot_refactored.py
```

### Option 3: Configuration File Only
If using configuration.json for all settings:
```bash
python3 discord_bot_refactored.py
```

## Testing Configuration

Run the test script to verify your setup:
```bash
python3 test_config.py
```

This will check:
- ✓ Configuration file loading
- ✓ Environment variables
- ✓ HD Wallet database (SQLite)
- ✓ Address derivation (testnet vs mainnet)
- ✓ Bot startup behavior

## HD Wallet Features

### Secure Entropy Storage
- BIP39 entropy (128-bit) stored in SQLite `wallet.db`
- Private keys derived on-demand, never stored permanently
- Addresses cached for performance

### Network-Specific Derivation
- Testnet addresses start with `m` or `n`
- Mainnet addresses start with `E`
- Same entropy produces different addresses per network

### Backup & Recovery
Use the `/backup` command (admin only) to retrieve mnemonic phrase for wallet recovery.

## Raw Transaction Workflow

The bot uses DeFi-Tome's raw transaction pattern:

1. **UTXO Selection** - Query address UTXOs via `getaddressutxos`
2. **Fee Estimation** - Calculate fees using `estimatesmartfee`
3. **Raw Transaction Creation** - Build transaction hex manually
4. **Signing** - Sign with WIF keys from HD wallet
5. **Mempool Testing** - Validate with `testmempoolaccept`
6. **Broadcast** - Send via `sendrawtransaction`

This eliminates reliance on node wallet management and provides full control over transaction construction.

## Troubleshooting

### Bot doesn't start
- Check that TOKEN is set correctly
- Verify TOKEN is not the placeholder value
- Check Discord developer portal for valid bot token

### RPC Connection Errors
- Verify RPC credentials in configuration.json
- Ensure RPC server is running and accessible
- Check firewall settings for RPC port
- Verify host/port match your RPC server configuration

### Wrong Network Addresses
- Check `"network"` setting in configuration.json
- Testnet: `"network": "testnet"`
- Mainnet: `"network": "mainnet"`
- Existing wallets will need new addresses if network is changed

### Database Issues
- Delete `wallet.db` to reset all wallets (users will get new entropy)
- Backup `wallet.db` before making changes

## Security Best Practices

1. **Never commit `.env` file** to version control
2. **Store RPC password** in `.env` rather than `configuration.json`
3. **Backup `wallet.db`** regularly for disaster recovery
4. **Use appropriate file permissions** on sensitive files
5. **Enable RPC whitelisting** on your Evrmore node
6. **Use HTTPS** for remote RPC connections (requires additional setup)

## Migration from Old Bot

If migrating from the original bot:
1. Export existing wallet data
2. Update configuration.json with new fields (`host`, `network`, `password`)
3. Run bot - new HD wallets will be created for users
4. Users should use `/backup` to save their new mnemonic phrases

## Support

For issues related to:
- **Bot functionality**: Check logs in `evr.log`
- **RPC errors**: Verify Evrmore node configuration
- **HD wallet issues**: Review DeFi-Tome documentation
- **Discord integration**: Check Discord developer documentation
