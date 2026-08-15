import os
import math
import asyncio
import logging
import sqlite3
import hashlib
import struct
from json import load
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Modal, TextInput
from requests import post
from base58 import b58decode_check, b58encode_check, b58decode, b58encode
from hdwallet import HDWallet, cryptocurrencies
from hdwallet.entropies import BIP39Entropy
from hdwallet.mnemonics import BIP39Mnemonic
from hdwallet.derivations import BIP44Derivation, CHANGES
from collections import OrderedDict

# ============================================================================
# CONFIGURATION & LOGGING
# ============================================================================

load_dotenv()
TOKEN = os.environ['TOKEN']
PASSWORD = os.environ['PASSWORD']

with open('configuration.json') as file:
    data = load(file)

ALLOWED_CHANNEL_IDS = data.get('allowed-channel-ids', [])
# Make channel locks optional - only enforce if list is non-empty
CHANNEL_LOCK_ENABLED = len(ALLOWED_CHANNEL_IDS) > 0
ALLOWED_CHANNEL_MENTIONS = ', '.join(f'<#{cid}>' for cid in ALLOWED_CHANNEL_IDS) if ALLOWED_CHANNEL_IDS else 'all channels'

BOTNAME = data['prefix']
BOTADDRESS = data['default-address']
ADMINID = data['admin-id']
BOTUUIDS = data['bot-uuids']
EVRID = data['evr-id']
UNOFFID = data['unoff-id']
LOG_FILE = data['log']
PERMISSIONS = data['permissions-integer']
NETWORK = data.get('network', 'mainnet').lower()
TXFEE = Decimal('0.01')
HOUSE = 'House'
RED = discord.Color.red()
GREEN = discord.Color.green()
PURPLE = discord.Color.purple()
QR = lambda qr: f'https://chart.apis.google.com/chart?cht=qr&chs=300x300&chl={qr}&choe=UTF-8&chld=L'
INVITE_URL = discord.utils.oauth_url(
    BOTUUIDS[0],
    permissions=discord.Permissions(PERMISSIONS),
    scopes=('bot', 'applications.commands')
)

SATOSHIS_PER_EVR = Decimal('100000000')
DUST_THRESHOLD_SATS = 546
DEFAULT_FEE_CONF_TARGET = 2
DEFAULT_FEE_ESTIMATE_MODE = 'CONSERVATIVE'

fh = logging.FileHandler(LOG_FILE)
fh.setLevel(logging.INFO)
ft = logging.Formatter('%(asctime)-15s - %(message)s')
fh.setFormatter(ft)

logger = logging.getLogger(BOTNAME)
logger.setLevel(logging.INFO)
logger.addHandler(fh)

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_wallet_db():
    """Initialize SQLite database for HD wallet entropy storage"""
    conn = sqlite3.connect('wallet.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_wallets (
            user_id TEXT PRIMARY KEY,
            entropy TEXT NOT NULL,
            passphrase TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS address_cache (
            user_id TEXT,
            account INTEGER DEFAULT 0,
            addr_index INTEGER DEFAULT 0,
            is_change INTEGER DEFAULT 0,
            address TEXT NOT NULL,
            wif TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, account, addr_index, is_change)
        )
    ''')
    conn.commit()
    conn.close()

init_wallet_db()

# ============================================================================
# RPC CLIENT (Public Endpoints Only - DeFi-Tome Pattern)
# ============================================================================

class PublicRpcClient:
    """Minimal JSON-RPC client for HTTPS public endpoints."""
    
    def __init__(self, url, timeout=10):
        self.url = str(url).rstrip('/')
        self.timeout = timeout
    
    def __getattr__(self, method_name):
        def _call(*args, **kwargs):
            params = list(args)
            if kwargs:
                params.append(kwargs)
            
            payload = {
                'jsonrpc': '1.0',
                'id': 'evrmorebot-public-rpc',
                'method': method_name,
                'params': params,
            }
            
            response = post(
                self.url,
                json=payload,
                timeout=self.timeout,
            )
            body = response.json()
            if body.get('error'):
                raise Exception(str(body['error']))
            response.raise_for_status()
            return body.get('result')
        
        return _call

# Use public RPC endpoint with network-aware URL
DEFAULT_RPC_URLS = {
    'mainnet': 'https://evr-rpc-mainnet.evrmorecoin.org/rpc',
    'testnet': 'https://evr-rpc-testnet.evrmorecoin.org/rpc'
}
RPC_URL = data.get('rpc_url', DEFAULT_RPC_URLS.get(NETWORK, DEFAULT_RPC_URLS['mainnet']))
rpc = PublicRpcClient(url=RPC_URL, timeout=30)

# ============================================================================
# HD WALLET MANAGEMENT
# ============================================================================

class HDWalletManager:
    """Manages HD wallet derivation with secure SQLite entropy storage"""
    
    def __init__(self, user_id):
        self.user_id = str(user_id)
        self.entropy = None
        self.passphrase = ''
        self._load_or_create_wallet()
    
    def _load_or_create_wallet(self):
        """Load existing wallet or create new one with fresh entropy"""
        conn = sqlite3.connect('wallet.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT entropy, passphrase FROM user_wallets WHERE user_id = ?', (self.user_id,))
        row = cursor.fetchone()
        
        if row:
            self.entropy = row[0]
            self.passphrase = row[1] or ''
        else:
            # Generate new 128-bit entropy (12 words)
            self.entropy = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
            cursor.execute(
                'INSERT INTO user_wallets (user_id, entropy, passphrase) VALUES (?, ?, ?)',
                (self.user_id, self.entropy, self.passphrase)
            )
            conn.commit()
            logger.info(f'Created new HD wallet for user {self.user_id}')
        
        conn.close()
    
    def _get_hd_wallet(self):
        """Create HDWallet instance from stored entropy"""
        mnemonic_str = BIP39Mnemonic.from_entropy(BIP39Entropy(self.entropy), language='english')
        mnemonic_obj = BIP39Mnemonic(mnemonic_str)
        return HDWallet(
            cryptocurrency=cryptocurrencies.Evrmore,
            passphrase=self.passphrase,
            network=NETWORK
        ).from_mnemonic(mnemonic_obj)
    
    def _derive_address(self, account=0, index=0, is_change=False):
        """Derive address at specified path"""
        change_chain = CHANGES.INTERNAL_CHAIN if is_change else CHANGES.EXTERNAL_CHAIN
        derivation = BIP44Derivation(
            cryptocurrencies.Evrmore.COIN_TYPE,
            account,
            change_chain,
            index
        )
        return self._get_hd_wallet().from_derivation(derivation)
    
    def get_address(self, account=0, index=0):
        """Get receive address, using cache if available"""
        conn = sqlite3.connect('wallet.db')
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT address, wif FROM address_cache WHERE user_id=? AND account=? AND addr_index=? AND is_change=0',
            (self.user_id, account, index)
        )
        row = cursor.fetchone()
        
        if row:
            conn.close()
            return row[0]
        
        # Derive and cache
        derived = self._derive_address(account, index, is_change=False)
        address = derived.address()
        wif = derived.wif()
        
        cursor.execute(
            'INSERT OR REPLACE INTO address_cache (user_id, account, addr_index, is_change, address, wif) VALUES (?, ?, ?, ?, ?, ?)',
            (self.user_id, account, index, 0, address, wif)
        )
        conn.commit()
        conn.close()
        
        return address
    
    def get_wif(self, account=0, index=0):
        """Get WIF for signing, using cache if available"""
        conn = sqlite3.connect('wallet.db')
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT wif FROM address_cache WHERE user_id=? AND account=? AND addr_index=? AND is_change=0',
            (self.user_id, account, index)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return row[0]
        
        # Derive and cache
        derived = self._derive_address(account, index, is_change=False)
        wif = derived.wif()
        
        # Cache it
        conn = sqlite3.connect('wallet.db')
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO address_cache (user_id, account, addr_index, is_change, address, wif) VALUES (?, ?, ?, ?, ?, ?)',
            (self.user_id, account, index, 0, derived.address(), wif)
        )
        conn.commit()
        conn.close()
        
        return wif
    
    def get_all_addresses(self, account=0, max_index=50):
        """Get all cached addresses for a user"""
        conn = sqlite3.connect('wallet.db')
        cursor = conn.cursor()
        cursor.execute(
            'SELECT address FROM address_cache WHERE user_id=? AND account=? AND is_change=0 ORDER BY addr_index',
            (self.user_id, account)
        )
        addresses = [row[0] for row in cursor.fetchall()]
        conn.close()
        return addresses
    
    def get_backup_phrase(self):
        """Return mnemonic phrase for backup"""
        mnemonic_str = BIP39Mnemonic.from_entropy(BIP39Entropy(self.entropy))
        return mnemonic_str

# ============================================================================
# RAW TRANSACTION HELPERS (DeFi-Tome Patterns)
# ============================================================================

def to_satoshis(amount):
    """Convert EVR to satoshis"""
    return int(Decimal(str(amount)) * SATOSHIS_PER_EVR)

def to_evr(satoshis):
    """Convert satoshis to EVR"""
    return Decimal(satoshis) / SATOSHIS_PER_EVR

def evr_output_value(amount):
    """Format EVR amount for raw transaction output"""
    return format(Decimal(str(amount)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN), 'f')

def p2pkh_script_pubkey(address):
    """Create P2PKH scriptPubKey"""
    decoded = b58decode_check(str(address))
    if len(decoded) != 21:
        raise Exception(f'Invalid P2PKH address: {address}')
    return b'\x76\xa9\x14' + decoded[1:] + b'\x88\xac'

def compact_size(value):
    """Encode CompactSize integer"""
    number = int(value)
    if number < 253:
        return bytes((number,))
    if number <= 0xFFFF:
        return b'\xfd' + struct.pack('<H', number)
    if number <= 0xFFFFFFFF:
        return b'\xfe' + struct.pack('<I', number)
    return b'\xff' + struct.pack('<Q', number)

def push_script_data(payload):
    """Push data onto script stack"""
    size = len(payload)
    if size < 76:
        return bytes((size,)) + payload
    if size <= 0xFF:
        return b'\x4c' + bytes((size,)) + payload
    if size <= 0xFFFF:
        return b'\x4d' + struct.pack('<H', size) + payload
    raise ValueError('Payload exceeds supported script size')

def create_raw_transaction(inputs, outputs, locktime=0):
    """Create raw transaction hex from inputs and outputs"""
    tx = b'\x02\x00\x00\x00'  # Version 2
    
    # Inputs
    tx += compact_size(len(inputs))
    for inp in inputs:
        tx += bytes.fromhex(inp['txid'])[::-1]
        tx += struct.pack('<I', inp['vout'])
        tx += b'\x00'  # scriptSig length
        tx += struct.pack('<I', inp.get('sequence', 0xFFFFFFFF))
    
    # Outputs
    tx += compact_size(len(outputs))
    for addr, amount in outputs.items():
        sat_amount = to_satoshis(amount)
        tx += struct.pack('<q', sat_amount)
        script = p2pkh_script_pubkey(addr)
        tx += compact_size(len(script)) + script
    
    # Locktime
    tx += struct.pack('<I', locktime)
    
    return tx.hex()

def get_address_utxos(address, asset_name=None):
    """Get UTXOs for an address"""
    request_obj = {'addresses': [address]}
    if asset_name:
        request_obj['assetName'] = str(asset_name)
    
    try:
        utxos = rpc.getaddressutxos(request_obj)
    except Exception as e:
        logger.debug(f'getaddressutxos with object failed: {e}, trying with positional arg')
        try:
            utxos = rpc.getaddressutxos([address])
        except:
            utxos = []
    
    if not isinstance(utxos, list):
        logger.warning(f'Unexpected UTXO response type: {type(utxos)}')
        return []
    return utxos

def select_evr_inputs(address, required_satoshis, locktime=0):
    """Select EVR UTXOs to fund a transaction"""
    utxos = get_address_utxos(address)
    selected = []
    total = 0
    
    # Filter for EVR-only UTXOs (no asset)
    evr_utxos = [u for u in utxos if not u.get('assetName')]
    
    for utxo in sorted(evr_utxos, key=lambda x: float(x.get('amount', 0)), reverse=True):
        if total >= required_satoshis:
            break
        txid = utxo.get('txid')
        vout = utxo.get('outputIndex', utxo.get('vout'))
        if txid and vout is not None:
            selected.append({
                'txid': txid,
                'vout': int(vout),
                'sequence': 0xFFFFFFFE if locktime else 0xFFFFFFFF
            })
            total += to_satoshis(utxo.get('amount', 0))
    
    if total < required_satoshis:
        raise Exception(f'Insufficient balance: need {required_satoshis} sats, have {total}')
    
    return selected, total

def estimate_fee(input_count, output_count, conf_target=2):
    """Estimate transaction fee"""
    try:
        feerate = rpc.estimatesmartfee(conf_target)
        if isinstance(feerate, dict) and 'feerate' in feerate:
            fee_per_kb = Decimal(str(feerate['feerate']))
        else:
            fee_per_kb = Decimal('0.0001')
    except:
        fee_per_kb = Decimal('0.0001')
    
    # Approximate tx size: 10 + 148*inputs + 34*outputs
    size_bytes = 10 + (input_count * 148) + (output_count * 34)
    size_kb = Decimal(size_bytes) / Decimal('1000')
    fee = (fee_per_kb * size_kb).quantize(Decimal('0.00000001'), rounding=ROUND_UP)
    
    # Ensure minimum fee
    min_fee = Decimal('0.0001')
    return max(fee, min_fee)

def sign_raw_transaction(raw_tx_hex, wif_keys):
    """Sign raw transaction with provided WIF keys"""
    try:
        signed = rpc.signrawtransaction(raw_tx_hex, [], wif_keys, 'ALL')
    except:
        signed = rpc.signrawtransaction(raw_tx_hex, None, wif_keys)
    
    if isinstance(signed, dict):
        if not signed.get('complete', True):
            errors = signed.get('errors', [])
            raise Exception(f'Signing incomplete: {errors}')
        return signed.get('hex')
    return str(signed)

def broadcast_transaction(signed_hex):
    """Broadcast signed transaction"""
    # Test mempool acceptance first
    try:
        accept_result = rpc.testmempoolaccept([signed_hex])
        if isinstance(accept_result, list) and len(accept_result) > 0:
            result = accept_result[0]
            if not result.get('allowed', False):
                reason = result.get('reject-reason') or result.get('reject_reason') or 'rejected'
                raise Exception(f'Transaction rejected: {reason}')
    except Exception as e:
        logger.warning(f'Mempool test skipped: {e}')
    
    return rpc.sendrawtransaction(signed_hex)

def create_and_send_evr(from_address, to_address, amount_evr, wif_keys):
    """Create, sign, and broadcast EVR transaction using raw workflow"""
    amount_sats = to_satoshis(amount_evr)
    fee = estimate_fee(1, 2)
    fee_sats = to_satoshis(fee)
    required = amount_sats + fee_sats
    
    inputs, total = select_evr_inputs(from_address, required)
    
    outputs = OrderedDict()
    outputs[to_address] = evr_output_value(amount_evr)
    
    # Change back to sender
    change_sats = total - amount_sats - fee_sats
    if change_sats >= DUST_THRESHOLD_SATS:
        outputs[from_address] = evr_output_value(to_evr(change_sats))
    
    raw_tx = create_raw_transaction(inputs, list(outputs.keys()))
    signed_tx = sign_raw_transaction(raw_tx, wif_keys)
    txid = broadcast_transaction(signed_tx)
    
    return {
        'txid': txid,
        'raw_tx': raw_tx,
        'signed_tx': signed_tx,
        'inputs': inputs,
        'outputs': dict(outputs)
    }

# ============================================================================
# DISCORD BOT SETUP
# ============================================================================

class ChannelLockedTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Channel lock is optional - only enforce if CHANNEL_LOCK_ENABLED is True
        if not CHANNEL_LOCK_ENABLED or interaction.channel_id in ALLOWED_CHANNEL_IDS:
            return True
        msg = f"Wrong room for that one — bring your commands to {ALLOWED_CHANNEL_MENTIONS}."
        await interaction.response.send_message(embed=embed_message('🚪 WRONG CHANNEL', msg, RED), ephemeral=True)
        return False

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents, tree_cls=ChannelLockedTree)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def embed_message(name, value, color):
    """Create Discord embed"""
    return discord.Embed(title=name, description=value, color=color)

def get_asset_balances(addresses):
    """Get asset balances for a list of addresses using getaddressutxos"""
    balances = {}
    for address in addresses:
        try:
            # Get all UTXOs for the address including assets
            utxos = get_address_utxos(address)
            for utxo in utxos:
                asset_name = utxo.get('assetName')
                if asset_name:
                    amount = float(utxo.get('amount', 0))
                    if asset_name in balances:
                        balances[asset_name] += amount
                    else:
                        balances[asset_name] = amount
        except Exception as e:
            logger.debug(f'Error getting asset balance for {address}: {e}')
            continue
    return balances

def is_valid_address(address):
    """Validate EVR address"""
    try:
        result = rpc.validateaddress(address)
        return result.get('isvalid', False)
    except:
        return False

def is_valid_amount(amount):
    """Validate amount"""
    return isinstance(amount, (int, float)) and math.isfinite(amount) and amount > 0

def current_time():
    """Get current UTC time"""
    return datetime.utcnow().strftime('%a, %b/%d/%Y - %H:%M:%S UTC')

# ============================================================================
# DISCORD COMMANDS
# ============================================================================

@bot.tree.command(name='menu', description='Open the interactive control panel')
async def menu_slash(interaction: discord.Interaction):
    user = interaction.user
    uuid = user.id
    wallet = HDWalletManager(uuid)
    
    class MenuView(View):
        @discord.ui.button(label='Balance Check', emoji='💰', style=discord.ButtonStyle.primary)
        async def balance(self, interaction: discord.Interaction, button: discord.ui.button):
            # Get all user addresses
            addresses = wallet.get_all_addresses()
            if not addresses:
                msg = 'No addresses found. Try /deposit first!'
                await interaction.response.send_message(embed=embed_message('⚠️ ERROR', msg, RED), ephemeral=True)
                return
            
            # Check balance via RPC
            try:
                total_balance = Decimal('0')
                for addr in addresses:
                    utxos = get_address_utxos(addr)
                    logger.debug(f'Found {len(utxos)} UTXOs for {addr}')
                    for utxo in utxos:
                        if not utxo.get('assetName'):
                            amount = Decimal(str(utxo.get('amount', 0)))
                            total_balance += amount
                            logger.debug(f'Added {amount} EVR from UTXO {utxo.get("txid")}')
                msg = f'{user.mention}, your vault is sitting at **{total_balance} $EVR**. Not bad.'
                await interaction.response.send_message(embed=embed_message('💰 BALANCE', msg, GREEN), ephemeral=True)
            except Exception as e:
                logger.error(f'Balance check error: {e}', exc_info=True)
                msg = 'Something glitched in the vault. Try again in a moment!'
                await interaction.response.send_message(embed=embed_message('⚠️ ERROR', msg, RED), ephemeral=True)
        
        @discord.ui.button(label='Asset Vault', emoji='🎒', style=discord.ButtonStyle.primary)
        async def asset_balance(self, interaction: discord.Interaction, button: discord.ui.button):
            addresses = wallet.get_all_addresses()
            if not addresses:
                msg = 'No addresses found. Try /deposit first!'
                await interaction.response.send_message(embed=embed_message('⚠️ ERROR', msg, RED), ephemeral=True)
                return
            
            asset_balances = get_asset_balances(addresses)
            logger.debug(f'Asset balances found: {asset_balances}')
            if asset_balances and len(asset_balances) > 0:
                embeds = []
                for asset, balance in asset_balances.items():
                    msg = f'`{asset}` — **{balance}**'
                    embeds.append(embed_message('🎒 ASSET VAULT', msg, GREEN))
                await interaction.response.send_message(embeds=embeds, ephemeral=True)
            else:
                msg = f'{user.mention}, your vault is empty for now — time to change that.'
                await interaction.response.send_message(embed=embed_message('🎒 ASSET VAULT', msg, RED), ephemeral=True)
        
        @discord.ui.button(label='Deposit', emoji='📥', style=discord.ButtonStyle.secondary)
        async def deposit(self, interaction: discord.Interaction, button: discord.ui.button):
            address = wallet.get_address()
            msg = f'{user.mention}, this is your door in. Send $EVR or assets straight here:'
            embed = discord.Embed(color=PURPLE)
            embed.add_field(name='📥 DEPOSIT ADDRESS', value=f'`{address}`', inline=False)
            embed.set_image(url=QR(address))
            embed.set_footer(text=address)
            await interaction.response.send_message(content=msg, embed=embed, ephemeral=True)
    
    view = MenuView()
    intro = discord.Embed(
        title='⚡ EVRMORE BOT — CONTROL PANEL',
        description=f'{user.mention}, you\'ve got the keys. Pick a move below and let\'s go.',
        color=PURPLE
    )
    await interaction.response.send_message(embed=intro, view=view, ephemeral=True)

@bot.tree.command(name='deposit', description='Get your deposit address for $EVR and assets')
async def deposit_slash(interaction: discord.Interaction):
    user = interaction.user
    wallet = HDWalletManager(user.id)
    address = wallet.get_address()
    msg = f'{user.mention}, this is your door in. Send $EVR or assets straight here:'
    embed = discord.Embed(color=PURPLE)
    embed.add_field(name='📥 DEPOSIT ADDRESS', value=f'`{address}`', inline=False)
    embed.set_image(url=QR(address))
    embed.set_footer(text=address)
    await interaction.response.send_message(content=msg, embed=embed, ephemeral=True)

@bot.tree.command(name='backup', description='Get your wallet backup phrase (KEEP THIS SECRET!)')
async def backup_slash(interaction: discord.Interaction):
    user = interaction.user
    if user.id != ADMINID:
        msg = "Backup phrases are sensitive — admin only!"
        await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
        return
    
    wallet = HDWalletManager(user.id)
    mnemonic = wallet.get_backup_phrase()
    msg = f'**⚠️ KEEP THIS SECRET ⚠️**\n\nYour backup phrase:\n```\n{mnemonic}\n```\n\nNever share this with anyone!'
    await interaction.response.send_message(embed=embed_message('🔐 WALLET BACKUP', msg, RED), ephemeral=True)

@bot.tree.command(name='send_evr', description='Send EVR using raw transaction workflow')
@app_commands.describe(address='Destination address', amount='Amount to send')
async def send_evr_slash(interaction: discord.Interaction, address: str, amount: float):
    user = interaction.user
    if not is_valid_address(address):
        msg = "That address doesn't look right — give it another look!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    
    if not is_valid_amount(amount):
        msg = 'Give me a real amount to work with!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    
    wallet = HDWalletManager(user.id)
    from_address = wallet.get_address()
    wif = wallet.get_wif()
    
    try:
        result = create_and_send_evr(from_address, address, amount, [wif])
        txid = result['txid']
        logger.info(f'{user.name}#{user.id} sent {amount} EVR to {address} TX: {txid}')
        msg = f'{user.mention} just sent `{amount}` $EVR to `{address}`.\nTXID: `{txid}`'
        await interaction.response.send_message(embed=embed_message('💸 SEND COMPLETE', msg, GREEN))
    except Exception as e:
        logger.error(f'Send error: {e}')
        msg = f'Something snapped: {str(e)}'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)

@bot.tree.command(name='info', description='Learn about the bot')
async def info_slash(interaction: discord.Interaction):
    msg = f"""
        This bot uses HD wallet technology with secure SQLite storage.
        
        **Key Features:**
        - BIP39/BIP44 HD wallet derivation
        - Entropy securely stored in SQLite
        - Raw transaction workflow (no local RPC wallet reliance)
        - MemPool acceptance testing before broadcast
        
        **Commands:**
        `/deposit` - Get your deposit address
        `/backup` - Get your backup phrase (admin only)
        `/send_evr` - Send EVR via raw transaction
        `/menu` - Open control panel
        
        *USE ME AT YOUR OWN RISK*
    """
    await interaction.response.send_message(embed=embed_message('📖 INFO', msg, GREEN), ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print('Bot is ready for use!')

@bot.event
async def on_message(message):
    if message is None:
        return
    user = message.author
    uuid = user.id
    msg = message.content
    logger.info(f'{uuid}: {msg}')

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error(f'Command "{interaction.command.name if interaction.command else "?"}" blew up: {error}', exc_info=error)
    msg = "Something snapped in the vault's wiring. Give it a beat and try again."
    embed = embed_message('⚠️ SYSTEM ERROR', msg, RED)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass

def main():
    bot.run(TOKEN)

if __name__ == '__main__':
    main()
