# Evrmore Bot Deployment Guide

## Prerequisites
- Python 3.8+ installed
- Discord Bot Token (get from https://discord.com/developers/applications)
- Evrmore node RPC credentials (username, password, port)
- Valid Evrmore wallet address

## Quick Start

### 1. Install Dependencies
```bash
pip install discord.py aiohttp beautifulsoup4 requests hdwallet python-dotenv
```

### 2. Configure Environment Variables
Edit `.env` file with your credentials:
```
TOKEN=your_discord_bot_token_here
PASSWORD=your_rpc_password_here
```

### 3. Configure Bot Settings
Edit `configuration.json` with your settings:
```json
{
    "default-address": "YOUR_EVR_WALLET_ADDRESS",
    "prefix": "Evrmore Bot",
    "user": "your-rpc-username",
    "port": 8819,
    "log": "evr.log",
    "tx-fee": 0.01,
    "admin-id": YOUR_DISCORD_USER_ID,
    "evr-id": YOUR_EVR_CURRENCY_ID,
    "unoff-id": YOUR_UNOFFICIAL_CURRENCY_ID
}
```

Replace placeholder values:
- `YOUR_EVR_WALLET_ADDRESS`: Your Evrmore wallet receive address
- `your-rpc-username`: RPC username from evrmore.conf
- `your_rpc_password_here`: RPC password from evrmore.conf
- `YOUR_DISCORD_USER_ID`: Your Discord user ID (right-click → Copy ID)
- `YOUR_EVR_CURRENCY_ID`: EVR currency ID in Discord server
- `YOUR_UNOFFICIAL_CURRENCY_ID`: Unofficial currency ID (optional)

### 4. Run the Bot
```bash
python3 discord_bot.py
```

## Testing Without Discord Connection

To test the bot logic without connecting to Discord:
```bash
python3 -c "
import discord_bot
# Test wallet initialization
discord_bot.init_wallet_db()
print('✓ Wallet DB initialized')

# Test HD wallet derivation
wallet = discord_bot.HDWalletManager('test_user_123')
addr = wallet.get_address(0, 0)
print(f'✓ Derived address: {addr}')

# Verify entropy is stored
import sqlite3
conn = sqlite3.connect('wallet.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM user_wallets WHERE user_id=?', ('test_user_123',))
count = cursor.fetchone()[0]
print(f'✓ Entropy stored: {count > 0}')
conn.close()
"
```

## Key Features

### HD Wallet Security
- BIP39 mnemonic phrase generated per user
- Entropy securely stored in SQLite (`wallet.db`)
- Addresses derived on-demand using BIP44 path
- WIF keys cached for performance, never stored permanently

### Raw Transaction Workflow
1. **UTXO Selection**: Uses `getaddressutxos` to find spendable outputs
2. **Raw Transaction Creation**: Builds transaction hex manually
3. **Fee Estimation**: Uses `estimatesmartfee` for dynamic fees
4. **Signing**: Signs with WIF from HD wallet
5. **Mempool Testing**: Validates with `testmempoolaccept` before broadcast
6. **Broadcast**: Sends via `sendrawtransaction`

### Discord Commands
- `/menu` - Interactive control panel
- `/deposit` - Generate deposit address
- `/backup` - Get mnemonic phrase (admin only)
- `/send_evr` - Send EVR using raw transactions
- `/transfer_asset` - Transfer assets
- `/mint_nft` - Mint NFTs via modal

## Troubleshooting

### "Improper token has been passed"
- Check that TOKEN in `.env` is valid
- Ensure bot is invited to your Discord server
- Verify bot permissions (Message Content Intent enabled)

### "Connection refused" to RPC
- Verify evrmore node is running
- Check RPC credentials in `configuration.json`
- Ensure RPC port (default 8819) is accessible

### Database Errors
- Delete `wallet.db` to reset (backup first!)
- Ensure write permissions in bot directory

## Security Notes

⚠️ **Important Security Practices:**
1. Never commit `.env` or `wallet.db` to version control
2. Store backup phrases securely offline
3. Use strong RPC passwords
4. Restrict bot permissions to necessary channels only
5. Regularly backup `wallet.db` for user recovery

## Production Deployment

For production use, consider:
1. Running in a systemd service or Docker container
2. Using environment variables instead of `.env` file
3. Setting up log rotation
4. Monitoring bot health and uptime
5. Implementing rate limiting for commands

See `start.sh`, `stop.sh` for example service management scripts.
