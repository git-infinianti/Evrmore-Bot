import os
import math
import asyncio
import logging
import sqlite3
import hashlib
import struct
import re
from typing import Dict, List, Optional, Set
from json import load
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_UP, InvalidOperation
from dotenv import load_dotenv
import aiohttp
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
BOTUUIDS = data['bot-uuids']


LOG_FILE = data['log']
PERMISSIONS = data['permissions-integer']
NETWORK = data.get('network', 'testnet').lower()
ALLOW_MENTIONS = data.get('allow_mentions', True)  # Configurable mention support
TXFEE = Decimal('0.01')
TXFEE_PER_KB_FLOOR = Decimal(str(data.get('tx-fee-per-kb', '0')))
EVR_TXFEE_PER_KB_STEP = Decimal('0.01025')
EVR_TXFEE_MAX_STEPS = 10
ASSET_TXFEE_PER_KB_STEP = Decimal('0.01025')
ASSET_TXFEE_MAX_STEPS = 10
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

async def acknowledge_interaction(interaction):
    """Defer an interaction and report whether followups are safe to use."""
    if interaction.response.is_done():
        return True

    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        return True
    except discord.InteractionResponded:
        return True
    except (discord.HTTPException, aiohttp.ClientError) as exc:
        logger.warning(f'Failed to acknowledge interaction {interaction.id}: {exc}')
        return False

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
RPC_URL = data.get('rpc-url', DEFAULT_RPC_URLS.get(NETWORK, DEFAULT_RPC_URLS['testnet']))
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

    def get_all_wifs(self, account=0):
        """Get signing keys for all cached external addresses in an account."""
        conn = sqlite3.connect('wallet.db')
        cursor = conn.cursor()
        cursor.execute(
            'SELECT wif FROM address_cache WHERE user_id=? AND account=? AND is_change=0 ORDER BY addr_index',
            (self.user_id, account)
        )
        wifs = [row[0] for row in cursor.fetchall()]
        conn.close()
        return wifs
    
    def get_backup_phrase(self):
        """Return mnemonic phrase for backup"""
        mnemonic_str = BIP39Mnemonic.from_entropy(BIP39Entropy(self.entropy), language='english')
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

    def utxo_satoshis(utxo):
        # Some endpoints return `satoshis`, others return EVR in `amount`.
        if 'satoshis' in utxo and utxo.get('satoshis') is not None:
            return int(Decimal(str(utxo.get('satoshis', 0))))
        return to_satoshis(utxo.get('amount', 0))
    
    # Keep EVR UTXOs (some nodes set assetName='EVR', others omit it).
    evr_utxos = [u for u in utxos if (not u.get('assetName')) or u.get('assetName') == 'EVR']
    
    for utxo in sorted(evr_utxos, key=utxo_satoshis, reverse=True):
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
            total += utxo_satoshis(utxo)
    
    if total < required_satoshis:
        # Get actual balance for better error message
        try:
            balance_result = rpc.getaddressbalance({'addresses': [address]})
            actual_balance = balance_result.get('balance', 0)
            raise Exception(f'Insufficient balance: need {required_satoshis} sats, have {actual_balance}')
        except:
            raise Exception(f'Insufficient balance: need {required_satoshis} sats, have {total}')
    
    return selected, total

def get_network_fee_rate():
    """Return the network-appropriate EVR/kB fee rate."""
    fee_per_kb = Decimal('0.01025')

    if NETWORK == 'mainnet':
        try:
            estimate = rpc.estimatesmartfee(6)
            if isinstance(estimate, dict) and estimate.get('feerate') is not None:
                estimated_rate = Decimal(str(estimate['feerate']))
                if estimated_rate > 0:
                    fee_per_kb = estimated_rate
        except Exception as exc:
            logger.warning(f'Mainnet smart fee estimate unavailable; using {fee_per_kb} EVR/kB: {exc}')

    # Respect network/mempool minimum relay fee floors.
    try:
        mempool_info = rpc.getmempoolinfo()
        if isinstance(mempool_info, dict) and 'mempoolminfee' in mempool_info:
            fee_per_kb = max(fee_per_kb, Decimal(str(mempool_info['mempoolminfee'])))
    except Exception:
        pass

    try:
        network_info = rpc.getnetworkinfo()
        if isinstance(network_info, dict) and 'relayfee' in network_info:
            fee_per_kb = max(fee_per_kb, Decimal(str(network_info['relayfee'])))
    except Exception:
        pass

    return fee_per_kb

def estimate_fee(input_count, output_count, conf_target=6):
    """Estimate transaction fee."""
    fee_per_kb = get_network_fee_rate()
    
    # Approximate tx size: 10 + 148*inputs + 34*outputs
    size_bytes = 10 + (input_count * 148) + (output_count * 34)
    size_kb = Decimal(size_bytes) / Decimal('1000')
    fee = (fee_per_kb * size_kb).quantize(Decimal('0.00000001'), rounding=ROUND_UP)
    
    # Honor configured operational fee floor to avoid strict relay policy rejections.
    min_fee = max(Decimal('0.001'), TXFEE)
    return max(fee, min_fee)

def estimate_fee_for_raw_tx(raw_tx_hex, input_count, conf_target=2, fee_per_kb_floor=None, minimum_fee=None, fee_per_kb_override=None):
    """Estimate fee from the actual raw tx size, plus expected P2PKH signatures."""
    fee_per_kb = (
        Decimal(str(fee_per_kb_override))
        if fee_per_kb_override is not None
        else get_network_fee_rate()
    )

    if fee_per_kb_floor is None and fee_per_kb_override is None:
        fee_per_kb_floor = TXFEE_PER_KB_FLOOR
    if fee_per_kb_floor is not None and fee_per_kb_floor > 0:
        fee_per_kb = max(fee_per_kb, fee_per_kb_floor)

    raw_size_bytes = Decimal(len(raw_tx_hex) // 2)
    estimated_signed_size = raw_size_bytes + (Decimal(input_count) * Decimal('108'))
    size_kb = estimated_signed_size / Decimal('1000')
    fee = (fee_per_kb * size_kb).quantize(Decimal('0.00000001'), rounding=ROUND_UP)

    min_fee = max(Decimal('0'), TXFEE if minimum_fee is None else Decimal(str(minimum_fee)))
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

def broadcast_transaction(signed_hex, diagnostic_context=None):
    """Broadcast signed transaction"""
    # Test mempool acceptance first
    try:
        accept_result = rpc.testmempoolaccept([signed_hex])
        if diagnostic_context is not None:
            signed_size_bytes = len(signed_hex) // 2
            input_evr_sats = int(diagnostic_context['input_evr_sats'])
            output_evr_sats = int(diagnostic_context['output_evr_sats'])
            actual_fee_sats = input_evr_sats - output_evr_sats
            effective_evr_per_kb = (
                Decimal(actual_fee_sats) * Decimal('1000')
                / (SATOSHIS_PER_EVR * Decimal(signed_size_bytes))
            )
            logger.info(
                '%s testmempoolaccept fee_rate_evr_per_kb=%s signed_bytes=%d '
                'input_evr_sats=%d output_evr_sats=%d actual_fee_sats=%d '
                'target_fee_sats=%d effective_evr_per_kb=%s response=%r',
                diagnostic_context.get('transaction_type', 'Transaction'),
                diagnostic_context['fee_rate_evr_per_kb'],
                signed_size_bytes,
                input_evr_sats,
                output_evr_sats,
                actual_fee_sats,
                diagnostic_context['target_fee_sats'],
                effective_evr_per_kb,
                accept_result,
            )
        if isinstance(accept_result, list) and len(accept_result) > 0:
            result = accept_result[0]
            if not result.get('allowed', False):
                reason = result.get('reject-reason') or result.get('reject_reason') or 'rejected'
                raise Exception(f'Transaction rejected: {reason}')
    except Exception as e:
        # If mempool test explicitly rejects the tx, surface it.
        if 'Transaction rejected:' in str(e):
            raise
        # Otherwise only skip when the precheck endpoint itself is unavailable.
        logger.warning(f'Mempool test skipped: {e}')
    
    return rpc.sendrawtransaction(signed_hex)

def create_and_send_evr(from_address, to_address, amount_evr, wif_keys):
    """Create, sign, and broadcast EVR transaction using raw workflow"""
    amount_sats = to_satoshis(amount_evr)
    last_error = None
    base_fee_rate = get_network_fee_rate()

    for fee_step in range(EVR_TXFEE_MAX_STEPS):
        fee_rate_evr_per_kb = base_fee_rate + (EVR_TXFEE_PER_KB_STEP * fee_step)
        try:
            estimated_size_bytes = 10 + 148 + (2 * 34)
            fee = (
                fee_rate_evr_per_kb * Decimal(estimated_size_bytes) / Decimal('1000')
            ).quantize(Decimal('0.00000001'), rounding=ROUND_UP)

            # Recompute fee after input selection since tx size depends on real input count.
            # Retry a few times if a higher fee requires more inputs.
            inputs = []
            total = 0
            for _ in range(3):
                fee_sats = to_satoshis(fee)
                required = amount_sats + fee_sats
                inputs, total = select_evr_inputs(from_address, required)

                # Assume change output first, then recompute if it would be dust.
                fee_with_change = (
                    fee_rate_evr_per_kb * Decimal(10 + (len(inputs) * 148) + (2 * 34)) / Decimal('1000')
                ).quantize(Decimal('0.00000001'), rounding=ROUND_UP)
                fee_without_change = (
                    fee_rate_evr_per_kb * Decimal(10 + (len(inputs) * 148) + 34) / Decimal('1000')
                ).quantize(Decimal('0.00000001'), rounding=ROUND_UP)
                change_with_change = total - amount_sats - to_satoshis(fee_with_change)

                if change_with_change >= DUST_THRESHOLD_SATS:
                    fee = fee_with_change
                    final_required = amount_sats + to_satoshis(fee)
                    if total >= final_required:
                        break
                else:
                    fee = fee_without_change
                    final_required = amount_sats + to_satoshis(fee)
                    if total >= final_required:
                        break

            fee_sats = to_satoshis(fee)
            outputs = OrderedDict()
            outputs[to_address] = evr_output_value(amount_evr)

            # Change back to sender
            change_sats = total - amount_sats - fee_sats
            if change_sats >= DUST_THRESHOLD_SATS:
                outputs[from_address] = evr_output_value(to_evr(change_sats))

            raw_tx = create_raw_transaction(inputs, outputs)
            signed_tx = sign_raw_transaction(raw_tx, wif_keys)
            output_evr_sats = sum(to_satoshis(value) for value in outputs.values())
            txid = broadcast_transaction(signed_tx, {
                'transaction_type': 'EVR',
                'fee_rate_evr_per_kb': fee_rate_evr_per_kb,
                'input_evr_sats': total,
                'output_evr_sats': output_evr_sats,
                'target_fee_sats': fee_sats,
            })

            return {
                'txid': txid,
                'raw_tx': raw_tx,
                'signed_tx': signed_tx,
                'inputs': inputs,
                'outputs': dict(outputs)
            }
        except Exception as e:
            last_error = e
            if 'min relay fee not met' in str(e).lower():
                logger.warning(f'EVR fee retry at {fee_rate_evr_per_kb} EVR/kB failed: {e}')
                continue
            raise

    if last_error:
        raise last_error
    raise Exception('Failed to create transaction')

def _normalize_utxo_satoshis(utxo):
    """Return integer satoshi/raw-unit amount from a UTXO-like object."""
    if 'satoshis' in utxo and utxo.get('satoshis') is not None:
        return int(Decimal(str(utxo.get('satoshis', 0))))
    return to_satoshis(utxo.get('amount', 0))

def _asset_decimal_places(value):
    text = format(Decimal(str(value)).normalize(), 'f')
    if '.' not in text:
        return 0
    return len(text.rsplit('.', 1)[1].rstrip('0'))

def _asset_utxo_display_amount(utxo, asset_name, asset_units):
    """Extract display-unit asset amount from a UTXO by preferring decoded chain data."""
    txid = utxo.get('txid')
    vout = utxo.get('outputIndex', utxo.get('vout'))

    if txid and vout is not None:
        try:
            tx = rpc.getrawtransaction(txid, True)
            if isinstance(tx, dict):
                for out in tx.get('vout', []):
                    if int(out.get('n', -1)) != int(vout):
                        continue
                    script = out.get('scriptPubKey', {})
                    asset_obj = script.get('asset', {})
                    decoded_name = str(asset_obj.get('name', '')).upper()
                    decoded_amount = asset_obj.get('amount')
                    if decoded_name == str(asset_name).upper() and decoded_amount is not None:
                        return Decimal(str(decoded_amount))
        except Exception:
            pass

    for field_name in ('amount', 'assetAmount', 'assetamount', 'asset_quantity', 'quantity'):
        if field_name not in utxo or utxo.get(field_name) is None:
            continue
        value = Decimal(str(utxo.get(field_name)))
        # If decimals exceed asset units, treat this as raw units and scale down.
        if _asset_decimal_places(value) > int(asset_units):
            scale = Decimal(10) ** int(asset_units)
            return value / scale
        return value

    if 'satoshis' in utxo and utxo.get('satoshis') is not None:
        scale = Decimal(10) ** int(asset_units)
        return Decimal(str(utxo.get('satoshis', 0))) / scale

    raise Exception('Unable to parse asset display amount from UTXO')

def _asset_utxo_evr_sats(utxo):
    """Extract EVR satoshis carried by an asset UTXO for fee funding."""
    txid = utxo.get('txid')
    vout = utxo.get('outputIndex', utxo.get('vout'))
    if not txid or vout is None:
        return 0

    try:
        tx = rpc.getrawtransaction(txid, True)
        if isinstance(tx, dict):
            for output in tx.get('vout', []):
                if int(output.get('n', -1)) == int(vout):
                    return to_satoshis(output.get('value', 0))
    except Exception as exc:
        logger.warning(f'Unable to read EVR value for asset UTXO {txid}:{vout}: {exc}')
    return 0

def select_asset_inputs(addresses, asset_name, required_amount, locktime=0):
    """Select asset UTXOs for the requested display-unit quantity."""
    target_asset = str(asset_name).upper()
    asset_units = get_asset_units(target_asset)
    gathered = []

    for address in addresses:
        utxos = get_address_utxos(address, asset_name=target_asset)
        for utxo in utxos:
            name = str(utxo.get('assetName', '')).upper()
            if name != target_asset:
                continue
            txid = utxo.get('txid')
            vout = utxo.get('outputIndex', utxo.get('vout'))
            if not txid or vout is None:
                continue
            gathered.append({
                'txid': txid,
                'vout': int(vout),
                'sequence': 0xFFFFFFFE if locktime else 0xFFFFFFFF,
                'asset_amount': _asset_utxo_display_amount(utxo, target_asset, asset_units),
                'evr_sats': _asset_utxo_evr_sats(utxo),
            })

    selected = []
    total_asset_amount = Decimal('0')
    total_evr_sats = 0
    for utxo in sorted(gathered, key=lambda u: u['asset_amount'], reverse=True):
        if total_asset_amount >= required_amount:
            break
        selected.append(utxo)
        total_asset_amount += utxo['asset_amount']
        total_evr_sats += utxo['evr_sats']

    if total_asset_amount < required_amount:
        raise Exception(f'Insufficient {target_asset} balance: need {required_amount}, have {total_asset_amount}')

    return selected, total_asset_amount, total_evr_sats

def select_evr_inputs_from_addresses(addresses, required_satoshis, excluded_outpoints=None, locktime=0):
    """Select EVR UTXOs across many addresses for fee funding."""
    excluded = excluded_outpoints or set()
    candidates = []

    for address in addresses:
        utxos = get_address_utxos(address)
        for utxo in utxos:
            asset_name = str(utxo.get('assetName', '')).upper()
            if asset_name not in ('', 'EVR'):
                continue
            txid = utxo.get('txid')
            vout = utxo.get('outputIndex', utxo.get('vout'))
            if not txid or vout is None:
                continue
            outpoint = (txid, int(vout))
            if outpoint in excluded:
                continue
            candidates.append({
                'txid': txid,
                'vout': int(vout),
                'sequence': 0xFFFFFFFE if locktime else 0xFFFFFFFF,
                'satoshis': _normalize_utxo_satoshis(utxo),
            })

    selected = []
    total = 0
    for utxo in sorted(candidates, key=lambda u: u['satoshis'], reverse=True):
        if total >= required_satoshis:
            break
        selected.append(utxo)
        total += utxo['satoshis']

    if total < required_satoshis:
        raise Exception(f'Insufficient EVR for fees: need {required_satoshis} sats, have {total}')

    return selected, total

def get_asset_units(asset_name):
    """Get asset precision (units) from chain metadata."""
    data = rpc.getassetdata(str(asset_name).upper())
    if not isinstance(data, dict):
        raise Exception(f'Invalid getassetdata response for {asset_name}')
    units = int(data.get('units', 0))
    if units < 0 or units > 8:
        raise Exception(f'Invalid units for {asset_name}: {units}')
    return units

def normalize_asset_amount(asset_name, amount_decimal):
    """Normalize a display-unit asset amount to declared asset precision."""
    units = get_asset_units(asset_name)
    normalized = amount_decimal.quantize(Decimal(10) ** -units)
    if normalized != amount_decimal:
        raise Exception(f'Amount precision exceeds {units} decimal places for {asset_name}')
    return normalized

def create_and_send_asset_raw(from_addresses, asset_change_address, evr_change_address, to_address, asset_name, amount_decimal, wif_keys):
    """Create, sign, and broadcast an asset transfer using raw-transaction RPC only."""
    normalized_asset = str(asset_name).strip().upper()
    amount_normalized = normalize_asset_amount(normalized_asset, amount_decimal)
    if amount_normalized <= 0:
        raise Exception('Asset amount must be greater than zero')
    if evr_change_address in (asset_change_address, to_address):
        raise Exception('EVR change address must be distinct from asset output addresses')

    asset_inputs, total_asset_amount, total_input_sats = select_asset_inputs(
        from_addresses,
        normalized_asset,
        amount_normalized,
    )

    asset_change_amount = total_asset_amount - amount_normalized
    last_error = None
    base_fee_rate = get_network_fee_rate()

    for fee_step in range(ASSET_TXFEE_MAX_STEPS):
        fee_rate_evr_per_kb = base_fee_rate + (ASSET_TXFEE_PER_KB_STEP * fee_step)
        try:
            selected_inputs = list(asset_inputs)
            selected_sats = int(total_input_sats)

            for _ in range(4):
                transfer_map = OrderedDict()
                if asset_change_amount > 0:
                    transfer_map[asset_change_address] = Decimal(str(asset_change_amount))
                transfer_map[to_address] = Decimal(str(transfer_map.get(to_address, Decimal('0')))) + amount_normalized

                tentative_outputs = OrderedDict()
                if selected_sats >= DUST_THRESHOLD_SATS:
                    tentative_outputs[evr_change_address] = evr_output_value(to_evr(selected_sats))
                for address, asset_amount in transfer_map.items():
                    tentative_outputs[address] = {'transfer': {normalized_asset: float(asset_amount)}}

                rpc_inputs = [{'txid': u['txid'], 'vout': u['vout'], 'sequence': u['sequence']} for u in selected_inputs]
                tentative_raw_tx = rpc.createrawtransaction(rpc_inputs, tentative_outputs)
                fee_sats = to_satoshis(
                    estimate_fee_for_raw_tx(
                        tentative_raw_tx,
                        len(selected_inputs),
                        minimum_fee=Decimal('0'),
                        fee_per_kb_override=fee_rate_evr_per_kb,
                    )
                )
                if selected_sats >= fee_sats:
                    break

                required_extra = fee_sats - selected_sats
                excluded = {(u['txid'], u['vout']) for u in selected_inputs}
                extra_inputs, extra_total = select_evr_inputs_from_addresses(from_addresses, required_extra, excluded_outpoints=excluded)
                selected_inputs.extend(extra_inputs)
                selected_sats += int(extra_total)
            else:
                raise Exception('Unable to fund EVR fee for asset transfer')

            transfer_map = OrderedDict()
            if asset_change_amount > 0:
                transfer_map[asset_change_address] = Decimal(str(asset_change_amount))
            transfer_map[to_address] = Decimal(str(transfer_map.get(to_address, Decimal('0')))) + amount_normalized

            outputs = OrderedDict()
            if selected_sats > 0:
                outputs[evr_change_address] = evr_output_value(to_evr(selected_sats))

            for address, asset_amount in transfer_map.items():
                outputs[address] = {'transfer': {normalized_asset: float(asset_amount)}}

            rpc_inputs = [{'txid': u['txid'], 'vout': u['vout'], 'sequence': u['sequence']} for u in selected_inputs]
            raw_tx = rpc.createrawtransaction(rpc_inputs, outputs)
            fee_sats = to_satoshis(
                estimate_fee_for_raw_tx(
                    raw_tx,
                    len(selected_inputs),
                    minimum_fee=Decimal('0'),
                    fee_per_kb_override=fee_rate_evr_per_kb,
                )
            )
            if selected_sats < fee_sats:
                raise Exception(f'Insufficient EVR for fees: need {fee_sats} sats, have {selected_sats}')

            evr_change_sats = selected_sats - fee_sats
            outputs = OrderedDict()
            if evr_change_sats >= DUST_THRESHOLD_SATS:
                outputs[evr_change_address] = evr_output_value(to_evr(evr_change_sats))
            for address, asset_amount in transfer_map.items():
                outputs[address] = {'transfer': {normalized_asset: float(asset_amount)}}

            raw_tx = rpc.createrawtransaction(rpc_inputs, outputs)
            signed_tx = sign_raw_transaction(raw_tx, wif_keys)
            output_evr_sats = sum(
                to_satoshis(value)
                for value in outputs.values()
                if not isinstance(value, dict)
            )
            txid = broadcast_transaction(signed_tx, {
                'fee_rate_evr_per_kb': fee_rate_evr_per_kb,
                'input_evr_sats': selected_sats,
                'output_evr_sats': output_evr_sats,
                'target_fee_sats': fee_sats,
            })

            return {
                'txid': txid,
                'raw_tx': raw_tx,
                'signed_tx': signed_tx,
                'fee_sats': fee_sats,
                'asset_amount': str(amount_normalized),
            }
        except Exception as e:
            last_error = e
            if 'min relay fee not met' in str(e).lower():
                logger.warning(f'Asset fee retry at {fee_rate_evr_per_kb} EVR/kB failed: {e}')
                continue
            raise

    if last_error:
        raise last_error
    raise Exception('Failed to create asset transfer transaction')

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

def get_address_balance(address):
    """Get the spendable native EVR balance for an address."""
    try:
        utxos = rpc.getaddressutxos({'addresses': [address]})
    except Exception:
        try:
            utxos = rpc.getaddressutxos([address])
        except Exception as exc:
            logger.error(f'Error getting balance for {address}: {exc}')
            raise

    if not isinstance(utxos, list):
        raise Exception(f'Unexpected UTXO response for {address}: {type(utxos).__name__}')

    balance_sats = sum(
        _normalize_utxo_satoshis(utxo)
        for utxo in utxos
        if str(utxo.get('assetName', '')).upper() in ('', 'EVR')
    )
    return to_evr(balance_sats)

def get_asset_balances(addresses):
    """Get asset balances for a list of addresses using listassetbalancesbyaddress"""
    balances = {}
    failed_addresses = []

    for address in addresses:
        try:
            # Evrmore RPC expects a single address argument for this method.
            result = rpc.listassetbalancesbyaddress(address)

            if isinstance(result, dict):
                # Documented shape: {"ASSET": qty, ...}
                for asset_name, amount in result.items():
                    if asset_name is None:
                        continue
                    amount_decimal = Decimal(str(amount))
                    if amount_decimal > 0:
                        balances[asset_name] = balances.get(asset_name, Decimal('0')) + amount_decimal
            elif isinstance(result, list):
                # Backward-compat: some providers may return a list of objects.
                for item in result:
                    asset_name = item.get('name') or item.get('assetName')
                    amount_decimal = Decimal(str(item.get('amount', 0)))
                    if asset_name and amount_decimal > 0:
                        balances[asset_name] = balances.get(asset_name, Decimal('0')) + amount_decimal
        except Exception as e:
            logger.debug(f'Error getting asset balances for {address}: {e}')
            failed_addresses.append(address)

    if not failed_addresses:
        return balances

    # Recover only addresses whose asset-balance RPC failed.
    asset_units_cache = {}
    for address in failed_addresses:
        try:
            utxos = get_address_utxos(address, asset_name='*')
            for utxo in utxos:
                asset_name = utxo.get('assetName')
                if not asset_name or asset_name == 'EVR':
                    continue

                if asset_name not in asset_units_cache:
                    asset_units_cache[asset_name] = get_asset_units(asset_name)
                amount_decimal = _asset_utxo_display_amount(
                    utxo,
                    asset_name,
                    asset_units_cache[asset_name],
                )

                if amount_decimal > 0:
                    balances[asset_name] = balances.get(asset_name, Decimal('0')) + amount_decimal
        except Exception as fallback_error:
            logger.debug(f'Fallback error for {address}: {fallback_error}')
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

def extract_discord_user_id(value):
    """Extract Discord user ID from <@id>, <@!id>, or raw numeric ID."""
    if value is None:
        return None

    text = str(value).strip()
    mention_match = re.fullmatch(r'<@!?(\d+)>', text)
    if mention_match:
        return mention_match.group(1)

    if text.isdigit():
        return text

    return None

def is_proxy_whitelist_error(error_obj):
    """Detect rpc-proxy whitelist denial errors."""
    if error_obj is None:
        return False
    return 'not in whitelist' in str(error_obj).lower()

def extract_vout_addresses(vout_entry):
    """Extract destination addresses from a verbose tx vout entry."""
    script = vout_entry.get('scriptPubKey', {}) if isinstance(vout_entry, dict) else {}
    addresses = []

    single = script.get('address')
    if isinstance(single, str) and single:
        addresses.append(single)

    many = script.get('addresses')
    if isinstance(many, list):
        addresses.extend([a for a in many if isinstance(a, str) and a])

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(addresses))

def get_address_txids(addresses):
    """Fetch transaction IDs for a set of addresses (newest first)."""
    try:
        result = rpc.getaddresstxids({'addresses': addresses})
    except Exception:
        result = rpc.getaddresstxids(addresses)

    if not isinstance(result, list):
        return []

    txids = []
    for item in result:
        if isinstance(item, str):
            txids.append(item)
        elif isinstance(item, dict):
            txid = item.get('txid')
            if isinstance(txid, str):
                txids.append(txid)

    # Most nodes return oldest -> newest for this endpoint; normalize to newest first.
    txids = list(dict.fromkeys(txids))
    txids.reverse()
    return txids

def format_tx_time(tx):
    """Render tx time in UTC if available."""
    stamp = tx.get('blocktime') or tx.get('time')
    if not stamp:
        return 'Unknown'
    try:
        return datetime.utcfromtimestamp(int(stamp)).strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return 'Unknown'

def summarize_transaction(txid, address_set, tx_cache, prev_tx_cache):
    """Build a summary for a transaction relative to a user's address set."""
    if txid in tx_cache:
        tx = tx_cache[txid]
    else:
        tx = rpc.getrawtransaction(txid, True)
        tx_cache[txid] = tx

    received = Decimal('0')
    sent = Decimal('0')

    for vout in tx.get('vout', []):
        vout_addrs = extract_vout_addresses(vout)
        if any(addr in address_set for addr in vout_addrs):
            received += Decimal(str(vout.get('value', 0)))

    for vin in tx.get('vin', []):
        prev_txid = vin.get('txid')
        prev_vout_index = vin.get('vout')
        if not prev_txid or prev_vout_index is None:
            continue

        if prev_txid in prev_tx_cache:
            prev_tx = prev_tx_cache[prev_txid]
        else:
            prev_tx = rpc.getrawtransaction(prev_txid, True)
            prev_tx_cache[prev_txid] = prev_tx

        prev_outputs = prev_tx.get('vout', [])
        if not isinstance(prev_outputs, list) or prev_vout_index >= len(prev_outputs):
            continue

        spent_output = prev_outputs[prev_vout_index]
        spent_addrs = extract_vout_addresses(spent_output)
        if any(addr in address_set for addr in spent_addrs):
            sent += Decimal(str(spent_output.get('value', 0)))

    net = received - sent
    if net > 0:
        direction = 'IN'
        amount = net
    elif net < 0:
        direction = 'OUT'
        amount = -net
    else:
        direction = 'SELF'
        amount = Decimal('0')

    reported_confirmations = int(tx.get('confirmations', 0) or 0)
    if reported_confirmations < 0:
        confirmations = reported_confirmations
        status = 'CONFLICTED'
    elif reported_confirmations > 0 and tx.get('blockhash'):
        confirmations = reported_confirmations
        status = 'CONFIRMED'
    else:
        confirmations = 0
        status = 'PENDING'

    return {
        'txid': txid,
        'direction': direction,
        'amount': amount,
        'confirmations': confirmations,
        'status': status,
        'time': format_tx_time(tx),
    }

class TransactionsPageSelect(discord.ui.Select):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        super().__init__(
            placeholder='Jump to page...',
            min_values=1,
            max_values=1,
            options=self._build_options(parent_view)
        )

    @staticmethod
    def _build_options(parent_view):
        total_pages = parent_view.total_pages
        active_page = parent_view.page_index
        total_txs = len(parent_view.txids)
        options = []
        for i in range(min(total_pages, 25)):
            start = (i * 5) + 1
            end = min((i + 1) * 5, total_txs)
            label = f'Page {i + 1}'
            description = f'Transactions {start}-{end}'
            options.append(discord.SelectOption(
                label=label,
                description=description,
                value=str(i),
                default=(i == active_page)
            ))
        return options

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            msg = 'This transaction view belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        self.parent_view.page_index = int(self.values[0])
        self.parent_view.refresh_select_options()
        await self.parent_view.update_message(interaction)

class TransactionsView(View):
    def __init__(self, owner_id, addresses, txids):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.addresses = addresses
        self.address_set: Set[str] = set(addresses)
        self.txids = txids
        self.page_size = 5
        self.page_index = 0
        self.total_pages = max(1, math.ceil(len(self.txids) / self.page_size))
        self.tx_cache: Dict[str, dict] = {}
        self.prev_tx_cache: Dict[str, dict] = {}
        self.message: Optional[discord.Message] = None

        self.page_select = TransactionsPageSelect(self)
        self.add_item(self.page_select)
        self._sync_button_state()

    def refresh_select_options(self):
        self.page_select.options = self.page_select._build_options(self)

    def _slice_for_page(self):
        start = self.page_index * self.page_size
        end = start + self.page_size
        return self.txids[start:end]

    def _sync_button_state(self):
        first_page = self.page_index <= 0
        last_page = self.page_index >= (self.total_pages - 1)
        self.first.disabled = first_page
        self.prev.disabled = first_page
        self.next.disabled = last_page
        self.last.disabled = last_page

    async def build_embed(self):
        embed = discord.Embed(
            title='📜 TRANSACTION HISTORY',
            color=PURPLE,
            description=f'Address scope: `{len(self.addresses)}` tracked addresses'
        )

        page_txids = self._slice_for_page()
        if not page_txids:
            embed.description = 'No transactions found yet for your wallet.'
            return embed

        for txid in page_txids:
            try:
                tx = summarize_transaction(txid, self.address_set, self.tx_cache, self.prev_tx_cache)
                amount_str = f"{tx['amount'].quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)} EVR"
                line1 = f"{tx['direction']} • {amount_str} • {tx['status']} ({tx['confirmations']} conf)"
                line2 = f"{tx['time']}"
                line3 = f"`{txid}`"
                value = f'{line1}\n{line2}\n{line3}'
            except Exception as e:
                logger.error(f'Failed to summarize tx {txid}: {e}')
                value = f'Could not decode transaction details.\n`{txid}`'

            embed.add_field(name='Transaction', value=value, inline=False)

        total_txs = len(self.txids)
        start = (self.page_index * self.page_size) + 1
        end = min((self.page_index + 1) * self.page_size, total_txs)
        embed.set_footer(text=f'Page {self.page_index + 1}/{self.total_pages} • Showing {start}-{end} of {total_txs}')
        return embed

    async def update_message(self, interaction: discord.Interaction):
        self._sync_button_state()
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label='Newest', style=discord.ButtonStyle.secondary, row=1)
    async def first(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This transaction view belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return
        self.page_index = 0
        self.refresh_select_options()
        await self.update_message(interaction)

    @discord.ui.button(label='Prev', style=discord.ButtonStyle.primary, row=1)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This transaction view belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return
        self.page_index = max(0, self.page_index - 1)
        self.refresh_select_options()
        await self.update_message(interaction)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This transaction view belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return
        self.page_index = min(self.total_pages - 1, self.page_index + 1)
        self.refresh_select_options()
        await self.update_message(interaction)

    @discord.ui.button(label='Oldest', style=discord.ButtonStyle.secondary, row=1)
    async def last(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This transaction view belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return
        self.page_index = self.total_pages - 1
        self.refresh_select_options()
        await self.update_message(interaction)

    @discord.ui.button(label='Refresh', style=discord.ButtonStyle.success, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This transaction view belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        self.txids = get_address_txids(self.addresses)
        self.total_pages = max(1, math.ceil(len(self.txids) / self.page_size))
        self.page_index = min(self.page_index, self.total_pages - 1)
        self.refresh_select_options()
        await self.update_message(interaction)

async def open_transactions_panel(interaction: discord.Interaction, user: discord.abc.User):
    """Load and render the paginated transaction history panel."""
    wallet = HDWalletManager(user.id)
    addresses = wallet.get_all_addresses()

    # Ensure we always include the primary receive address.
    primary = wallet.get_address()
    if primary not in addresses:
        addresses.insert(0, primary)

    txids = get_address_txids(addresses)
    if not txids:
        msg = f'{user.mention}, no transactions found yet for your wallet.'
        await interaction.followup.send(embed=embed_message('📭 NO TRANSACTIONS', msg, RED), ephemeral=True)
        return

    view = TransactionsView(owner_id=user.id, addresses=addresses, txids=txids)
    embed = await view.build_embed()
    sent_message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    view.message = sent_message

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
            
            # Check balance via RPC using getaddressbalance
            try:
                total_balance = Decimal('0')
                for addr in addresses:
                    balance = get_address_balance(addr)
                    total_balance += balance
                    logger.debug(f'Balance for {addr}: {balance} EVR')
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
            
            # Filter out EVR from asset balances - only show non-EVR assets
            non_evr_assets = {asset: balance for asset, balance in asset_balances.items() if asset != 'EVR'}
            
            if non_evr_assets and len(non_evr_assets) > 0:
                embeds = []
                for asset, balance in non_evr_assets.items():
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

        @discord.ui.button(label='Transactions', emoji='📜', style=discord.ButtonStyle.secondary)
        async def transactions(self, interaction: discord.Interaction, button: discord.ui.button):
            if not await acknowledge_interaction(interaction):
                return

            try:
                await open_transactions_panel(interaction, interaction.user)
            except Exception as e:
                logger.error(f'Transactions menu button error: {e}', exc_info=True)
                msg = 'Could not load transaction history right now. Try again in a moment.'
                await interaction.followup.send(embed=embed_message('⚠️ ERROR', msg, RED), ephemeral=True)
    
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

    wallet = HDWalletManager(user.id)
    mnemonic = wallet.get_backup_phrase()
    msg = f'**⚠️ KEEP THIS SECRET ⚠️**\n\nYour backup phrase:\n```\n{mnemonic}\n```\n\nNever share this with anyone!'
    await interaction.response.send_message(embed=embed_message('🔐 WALLET BACKUP', msg, RED), ephemeral=True)

class SendEvrModal(Modal, title='Send EVR'):
    destination = TextInput(
        label='Destination Address',
        placeholder='EVR address',
        required=True,
        max_length=128,
    )
    amount = TextInput(
        label='Amount (EVR)',
        placeholder='10.0',
        required=True,
        max_length=32,
    )

    def __init__(self, owner_id):
        super().__init__()
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        if user.id != self.owner_id:
            msg = 'This send form belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        raw_destination = str(self.destination.value).strip()
        raw_amount = str(self.amount.value).strip()

        try:
            amount = float(raw_amount)
        except ValueError:
            msg = 'Amount must be a valid number.'
            await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        if not is_valid_amount(amount):
            msg = 'Give me a real amount to work with!'
            await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        if not await acknowledge_interaction(interaction):
            return

        to_address = raw_destination

        if not is_valid_address(to_address):
            msg = "That address doesn't look right — give it another look!"
            await interaction.followup.send(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        wallet = HDWalletManager(user.id)
        from_address = wallet.get_address()
        wif = wallet.get_wif()

        try:
            result = create_and_send_evr(from_address, to_address, amount, [wif])
            txid = result['txid']
            logger.info(f'{user.name}#{user.id} sent {amount} EVR to {to_address} TX: {txid}')
            recipient_display = f'`{to_address}`'
            msg = f'{user.mention} just sent `{amount}` $EVR to {recipient_display}.\nTXID: `{txid}`'
            await interaction.followup.send(embed=embed_message('💸 SEND COMPLETE', msg, GREEN), ephemeral=True)
        except Exception as e:
            logger.error(f'Send error: {e}', exc_info=True)
            msg = f'Something snapped: {str(e)}'
            await interaction.followup.send(embed=embed_message('ERROR', msg, RED), ephemeral=True)

class SendEvrToUserModal(Modal, title='Send EVR to User'):
    def __init__(self, owner_id, recipient_user_id, recipient_display, destination_address):
        super().__init__()
        self.owner_id = int(owner_id)
        self.recipient_user_id = str(recipient_user_id)
        self.recipient_display = str(recipient_display)
        self.destination_address = str(destination_address).strip()

        self.destination = TextInput(
            label='Destination Address (Auto-Filled)',
            default=self.destination_address,
            required=True,
            max_length=128,
        )
        self.amount = TextInput(
            label='Amount (EVR)',
            placeholder='10.0',
            required=True,
            max_length=32,
        )
        self.add_item(self.destination)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        if user.id != self.owner_id:
            msg = 'This send form belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        raw_amount = str(self.amount.value).strip()
        try:
            amount = float(raw_amount)
        except ValueError:
            msg = 'Amount must be a valid number.'
            await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        if not is_valid_amount(amount):
            msg = 'Give me a real amount to work with!'
            await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        if not await acknowledge_interaction(interaction):
            return

        to_address = self.destination_address

        wallet = HDWalletManager(user.id)
        from_address = wallet.get_address()
        wif = wallet.get_wif()

        try:
            result = create_and_send_evr(from_address, to_address, amount, [wif])
            txid = result['txid']
            logger.info(f'{user.name}#{user.id} sent {amount} EVR to {to_address} TX: {txid}')
            msg = f"{user.mention} just sent `{amount}` $EVR to {self.recipient_display}.\nTXID: `{txid}`"
            await interaction.followup.send(embed=embed_message('💸 SEND COMPLETE', msg, GREEN), ephemeral=True)
        except Exception as e:
            logger.error(f'Send error: {e}', exc_info=True)
            msg = f'Something snapped: {str(e)}'
            await interaction.followup.send(embed=embed_message('ERROR', msg, RED), ephemeral=True)

class SendEvrUserSelect(discord.ui.UserSelect):
    def __init__(self, owner_id):
        self.owner_id = int(owner_id)
        super().__init__(placeholder='Choose a user to send EVR to...', min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            msg = 'This send menu belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        selected_user = self.values[0]
        recipient_display = f'<@{selected_user.id}>'
        try:
            target_wallet = HDWalletManager(selected_user.id)
            to_address = target_wallet.get_address()
        except Exception as e:
            logger.error(f'Failed to resolve selected user {selected_user.id}: {e}')
            msg = "Couldn't generate a vault address for that user right now. Try again in a moment!"
            await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        await interaction.response.send_modal(
            SendEvrToUserModal(self.owner_id, selected_user.id, recipient_display, to_address)
        )

class SendEvrRecipientView(View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.add_item(SendEvrUserSelect(owner_id))

    @discord.ui.button(label='Use Address Instead', emoji='🏷️', style=discord.ButtonStyle.secondary)
    async def manual_address(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This send menu belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return
        await interaction.response.send_modal(SendEvrModal(self.owner_id))

class SendAssetModal(Modal, title='Send Asset'):
    destination = TextInput(
        label='Destination Address',
        placeholder='EVR address',
        required=True,
        max_length=128,
    )
    amount = TextInput(
        label='Amount (asset units)',
        placeholder='1.0',
        required=True,
        max_length=32,
    )

    def __init__(self, owner_id, asset_name):
        super().__init__()
        self.owner_id = int(owner_id)
        self.asset_name = str(asset_name).strip().upper()

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        if user.id != self.owner_id:
            msg = 'This send form belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        raw_destination = str(self.destination.value).strip()
        raw_amount = str(self.amount.value).strip()

        if not self.asset_name:
            await interaction.response.send_message(embed=embed_message('ERROR', 'Asset name is required.', RED), ephemeral=True)
            return

        try:
            amount_decimal = Decimal(raw_amount)
        except InvalidOperation:
            await interaction.response.send_message(embed=embed_message('ERROR', 'Amount must be a valid number.', RED), ephemeral=True)
            return

        if amount_decimal <= 0:
            await interaction.response.send_message(embed=embed_message('ERROR', 'Amount must be greater than zero.', RED), ephemeral=True)
            return

        if not await acknowledge_interaction(interaction):
            return

        to_address = raw_destination

        if not is_valid_address(to_address):
            await interaction.followup.send(embed=embed_message('ERROR', "That address doesn't look right — give it another look!", RED), ephemeral=True)
            return

        wallet = HDWalletManager(user.id)
        from_addresses = wallet.get_all_addresses()
        primary = wallet.get_address()
        if primary not in from_addresses:
            from_addresses.insert(0, primary)
        if not from_addresses:
            from_addresses = [primary]

        # Fast balance sanity check before RPC submit.
        balances = get_asset_balances(from_addresses)
        available = Decimal(str(balances.get(self.asset_name, Decimal('0'))))
        if available < amount_decimal:
            msg = f'Insufficient {self.asset_name} balance. Need {amount_decimal}, have {available}.'
            await interaction.followup.send(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        try:
            evr_change_index = 1 if wallet.get_address(index=1) != to_address else 2
            evr_change_address = wallet.get_address(index=evr_change_index)
            result = create_and_send_asset_raw(
                from_addresses,
                primary,
                evr_change_address,
                to_address,
                self.asset_name,
                amount_decimal,
                wallet.get_all_wifs(),
            )
            txid = result['txid']
        except Exception as e:
            logger.error(f'Asset send error ({self.asset_name}): {e}')
            if is_proxy_whitelist_error(e):
                msg = (
                    f'Asset raw-transaction workflow is blocked by this RPC proxy whitelist for {self.asset_name}. '
                    'Ask the proxy operator to allow raw transaction methods '
                    '(getaddressutxos, getassetdata, createrawtransaction, signrawtransaction, sendrawtransaction).'
                )
            else:
                msg = (
                    f'Asset raw-transaction send failed for {self.asset_name}: {e}'
                )
            await interaction.followup.send(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        logger.info(f'{user.name}#{user.id} sent {amount_decimal} {self.asset_name} to {to_address} TX: {txid}')
        recipient_display = f'`{to_address}`'
        msg = f'{user.mention} just sent `{amount_decimal}` `{self.asset_name}` to {recipient_display}.\nTXID: `{txid}`'
        await interaction.followup.send(embed=embed_message('✅ ASSET SEND COMPLETE', msg, GREEN), ephemeral=True)

class SendAssetToUserModal(Modal, title='Send Asset to User'):
    def __init__(self, owner_id, asset_name, recipient_display, destination_address):
        super().__init__()
        self.owner_id = int(owner_id)
        self.asset_name = str(asset_name).strip().upper()
        self.recipient_display = str(recipient_display)
        self.destination_address = str(destination_address).strip()

        self.destination = TextInput(
            label='Destination Address (Auto-Filled)',
            default=self.destination_address,
            required=True,
            max_length=128,
        )
        self.amount = TextInput(
            label='Amount (asset units)',
            placeholder='1.0',
            required=True,
            max_length=32,
        )
        self.add_item(self.destination)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        if user.id != self.owner_id:
            msg = 'This send form belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        raw_amount = str(self.amount.value).strip()

        if not self.asset_name:
            await interaction.response.send_message(embed=embed_message('ERROR', 'Asset name is required.', RED), ephemeral=True)
            return

        try:
            amount_decimal = Decimal(raw_amount)
        except InvalidOperation:
            await interaction.response.send_message(embed=embed_message('ERROR', 'Amount must be a valid number.', RED), ephemeral=True)
            return

        if amount_decimal <= 0:
            await interaction.response.send_message(embed=embed_message('ERROR', 'Amount must be greater than zero.', RED), ephemeral=True)
            return

        if not await acknowledge_interaction(interaction):
            return

        to_address = self.destination_address
        if not is_valid_address(to_address):
            await interaction.followup.send(embed=embed_message('ERROR', "That address doesn't look right — give it another look!", RED), ephemeral=True)
            return

        wallet = HDWalletManager(user.id)
        from_addresses = wallet.get_all_addresses()
        primary = wallet.get_address()
        if primary not in from_addresses:
            from_addresses.insert(0, primary)
        if not from_addresses:
            from_addresses = [primary]

        balances = get_asset_balances(from_addresses)
        available = Decimal(str(balances.get(self.asset_name, Decimal('0'))))
        if available < amount_decimal:
            msg = f'Insufficient {self.asset_name} balance. Need {amount_decimal}, have {available}.'
            await interaction.followup.send(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        try:
            evr_change_index = 1 if wallet.get_address(index=1) != to_address else 2
            evr_change_address = wallet.get_address(index=evr_change_index)
            result = create_and_send_asset_raw(
                from_addresses,
                primary,
                evr_change_address,
                to_address,
                self.asset_name,
                amount_decimal,
                wallet.get_all_wifs(),
            )
            txid = result['txid']
        except Exception as e:
            logger.error(f'Asset send error ({self.asset_name}): {e}')
            if is_proxy_whitelist_error(e):
                msg = (
                    f'Asset raw-transaction workflow is blocked by this RPC proxy whitelist for {self.asset_name}. '
                    'Ask the proxy operator to allow raw transaction methods '
                    '(getaddressutxos, getassetdata, createrawtransaction, signrawtransaction, sendrawtransaction).'
                )
            else:
                msg = (
                    f'Asset raw-transaction send failed for {self.asset_name}: {e}'
                )
            await interaction.followup.send(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        logger.info(f'{user.name}#{user.id} sent {amount_decimal} {self.asset_name} to {to_address} TX: {txid}')
        msg = f'{user.mention} just sent `{amount_decimal}` `{self.asset_name}` to {self.recipient_display}.\nTXID: `{txid}`'
        await interaction.followup.send(embed=embed_message('✅ ASSET SEND COMPLETE', msg, GREEN), ephemeral=True)

class SendAssetUserSelect(discord.ui.UserSelect):
    def __init__(self, owner_id, asset_name):
        self.owner_id = int(owner_id)
        self.asset_name = str(asset_name).strip().upper()
        super().__init__(placeholder='Choose a user to send this asset to...', min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            msg = 'This send menu belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        selected_user = self.values[0]
        recipient_display = f'<@{selected_user.id}>'
        try:
            target_wallet = HDWalletManager(selected_user.id)
            to_address = target_wallet.get_address()
        except Exception as e:
            logger.error(f'Failed to resolve selected user {selected_user.id}: {e}')
            msg = "Couldn't generate a vault address for that user right now. Try again in a moment!"
            await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            return

        await interaction.response.send_modal(
            SendAssetToUserModal(self.owner_id, self.asset_name, recipient_display, to_address)
        )

class SendAssetRecipientView(View):
    def __init__(self, owner_id, asset_name):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.asset_name = str(asset_name).strip().upper()
        self.add_item(SendAssetUserSelect(owner_id, asset_name))

    @discord.ui.button(label='Use Address Instead', emoji='🏷️', style=discord.ButtonStyle.secondary)
    async def manual_address(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This send menu belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return
        await interaction.response.send_modal(SendAssetModal(self.owner_id, self.asset_name))

class SendAssetSelect(discord.ui.Select):
    def __init__(self, owner_id, asset_balances):
        self.owner_id = int(owner_id)
        sorted_assets = sorted(asset_balances.items(), key=lambda item: item[0])
        options = []
        for asset_name, balance in sorted_assets[:25]:
            balance_str = str(Decimal(str(balance)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN))
            options.append(discord.SelectOption(
                label=asset_name,
                description=f'Balance: {balance_str}',
                value=asset_name,
            ))

        super().__init__(
            placeholder='Select an asset to send...',
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            msg = 'This send menu belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return
        asset_name = self.values[0]
        msg = f'Pick a recipient for {asset_name}, or choose address mode.'
        await interaction.response.send_message(
            embed=embed_message('🧪 SEND ASSET', msg, PURPLE),
            view=SendAssetRecipientView(self.owner_id, asset_name),
            ephemeral=True,
        )

class SendAssetSelectView(View):
    def __init__(self, owner_id, asset_balances):
        super().__init__(timeout=300)
        self.add_item(SendAssetSelect(owner_id, asset_balances))

class SendMenuView(View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)

    @discord.ui.button(label='EVR', emoji='💸', style=discord.ButtonStyle.primary)
    async def send_evr(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This send menu belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return
        msg = 'Pick a recipient from the user picker, or choose address mode.'
        await interaction.response.send_message(
            embed=embed_message('💸 SEND EVR', msg, PURPLE),
            view=SendEvrRecipientView(self.owner_id),
            ephemeral=True,
        )

    @discord.ui.button(label='Asset', emoji='🧪', style=discord.ButtonStyle.secondary)
    async def send_asset(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            msg = 'This send menu belongs to someone else.'
            await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
            return

        if not await acknowledge_interaction(interaction):
            return

        wallet = HDWalletManager(interaction.user.id)
        addresses = wallet.get_all_addresses()
        primary = wallet.get_address()
        if primary not in addresses:
            addresses.insert(0, primary)

        balances = get_asset_balances(addresses)
        asset_balances = {
            name: amount
            for name, amount in balances.items()
            if name and name != 'EVR' and Decimal(str(amount)) > 0
        }

        if not asset_balances:
            msg = 'No non-EVR assets found in your wallet yet.'
            await interaction.followup.send(embed=embed_message('🎒 NO ASSETS', msg, RED), ephemeral=True)
            return

        total_assets = len(asset_balances)
        description = 'Choose an asset to continue.'
        if total_assets > 25:
            description += f' Showing first 25 of {total_assets} assets.'

        embed = embed_message('🧪 ASSET SEND', description, PURPLE)
        await interaction.followup.send(
            embed=embed,
            view=SendAssetSelectView(self.owner_id, asset_balances),
            ephemeral=True,
        )

@bot.tree.command(name='send', description='Open send menu for EVR and assets')
async def send_slash(interaction: discord.Interaction):
    user = interaction.user

    if not await acknowledge_interaction(interaction):
        return

    try:
        wallet = HDWalletManager(user.id)
        addresses = wallet.get_all_addresses()
        primary = wallet.get_address()
        if primary not in addresses:
            addresses.insert(0, primary)

        total_balance = Decimal('0')
        for address in addresses:
            total_balance += get_address_balance(address)

        balance_text = total_balance.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        intro = discord.Embed(
            title='💸 SEND PANEL',
            description=(
                f'{user.mention}, pick what you want to send.\n'
                f'Current EVR balance: **{balance_text} EVR**'
            ),
            color=PURPLE,
        )
        intro.set_footer(text='EVR and Asset buttons open forms for destination and amount.')

        await interaction.followup.send(embed=intro, view=SendMenuView(user.id), ephemeral=True)
    except Exception as e:
        logger.error(f'Send menu error: {e}', exc_info=True)
        msg = 'Could not open the send panel right now. Try again in a moment.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed_message('ERROR', msg, RED), ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        except (discord.HTTPException, aiohttp.ClientError):
            pass

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
        `/backup` - Get your wallet backup phrase
        `/send` - Open the send menu (user picker or direct address mode)
        `/menu` - Open control panel (includes transaction history browser)
        
        *USE ME AT YOUR OWN RISK*
    """
    await interaction.response.send_message(embed=embed_message('📖 INFO', msg, GREEN), ephemeral=True)

@bot.event
async def on_ready():
    # Global sync can take time to propagate. Also sync per-guild for immediate updates.
    await bot.tree.sync()
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
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
    except (discord.HTTPException, aiohttp.ClientError):
        pass

def main():
    bot.run(TOKEN)

if __name__ == '__main__':
    main()
