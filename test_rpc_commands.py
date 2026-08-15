#!/usr/bin/env python3
"""
Test script to verify RPC commands work with the new configuration
Tests both testnet and mainnet endpoints
"""

import json
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def load_config():
    """Load configuration from file"""
    try:
        with open('configuration.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        return None

def test_rpc_connection(config, network_name="Test"):
    """Test basic RPC connection"""
    import requests
    from requests.auth import HTTPBasicAuth
    
    host = config.get('host', 'https://testnet-rpc.evrmorecoin.org')
    port = config.get('port', 443)
    username = os.getenv('RPC_USERNAME', 'evrmoreuser')
    password = os.getenv('PASSWORD', '')
    
    # Handle public RPC endpoints (HTTPS, port 443)
    if 'evrmorecoin.org' in host or host.startswith('https://'):
        # Public endpoints use HTTPS on port 443, don't append port
        url = host.rstrip('/')
        auth = None
        print(f"🌐 Testing public RPC endpoint: {url}")
    else:
        url = f"http://{host}:{port}"
        auth = HTTPBasicAuth(username, password)
        print(f"🔒 Testing private RPC endpoint: {url}")
    
    payload = {
        "jsonrpc": "1.0",
        "id": "test",
        "method": "getblockchaininfo",
        "params": []
    }
    
    headers = {'content-type': 'application/json'}
    
    try:
        response = requests.post(url, json=payload, headers=headers, auth=auth, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if 'result' in result:
                blockchain_info = result['result']
                print(f"✅ RPC Connection successful!")
                print(f"   Network: {blockchain_info.get('chain', 'unknown')}")
                print(f"   Blocks: {blockchain_info.get('blocks', 0)}")
                print(f"   Difficulty: {blockchain_info.get('difficulty', 0)}")
                return True
            else:
                print(f"❌ RPC returned error: {result.get('error', 'Unknown error')}")
                return False
        elif response.status_code == 404:
            print(f"⚠️  RPC endpoint returned 404 - This may indicate:")
            print(f"   - Public RPC server requires authentication")
            print(f"   - Endpoint URL is incorrect")
            print(f"   - Server is behind a proxy/firewall")
            print(f"   Try using a local Evrmore node instead")
            return False
        else:
            print(f"❌ HTTP Error: {response.status_code} - {response.text[:200]}")
            return False
            
    except requests.exceptions.ConnectionError as e:
        print(f"❌ Connection failed: {e}")
        print(f"   Make sure the RPC server is running at {url}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def test_getaddressutxos(config):
    """Test getaddressutxos command (critical for raw transactions)"""
    import requests
    from requests.auth import HTTPBasicAuth
    
    host = config.get('host', 'https://testnet-rpc.evrmorecoin.org')
    port = config.get('port', 443)
    username = os.getenv('RPC_USERNAME', 'evrmoreuser')
    password = os.getenv('PASSWORD', '')
    wallet_address = config.get('wallet_address', '')
    
    if not wallet_address:
        print("⚠️  Skipping getaddressutxos test - no wallet address configured")
        return True
    
    # Handle public RPC endpoints
    if 'evrmorecoin.org' in host or host.startswith('https://'):
        url = host.rstrip('/')
        auth = None
    else:
        url = f"http://{host}:{port}"
        auth = HTTPBasicAuth(username, password)
    
    payload = {
        "jsonrpc": "1.0",
        "id": "test-utxos",
        "method": "getaddressutxos",
        "params": [{"addresses": [wallet_address]}]
    }
    
    headers = {'content-type': 'application/json'}
    
    try:
        response = requests.post(url, json=payload, headers=headers, auth=auth, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if 'result' in result:
                utxos = result['result']
                print(f"✅ getaddressutxos successful!")
                print(f"   Found {len(utxos)} UTXOs for address {wallet_address[:10]}...")
                if utxos:
                    print(f"   First UTXO: {utxos[0]['txid'][:16]}... : {utxos[0]['satoshis']} satoshis")
                return True
            else:
                print(f"❌ RPC returned error: {result.get('error', 'Unknown error')}")
                return False
        else:
            print(f"❌ HTTP Error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ getaddressutxos failed: {e}")
        return False

def test_hd_wallet():
    """Test HD wallet derivation"""
    try:
        from hdwallet import HDWallet
        from hdwallet.mnemonics import BIP39Mnemonic
        from hdwallet.derivations import BIP44Derivation
        from hdwallet.cryptocurrencies import Evrmore
        
        config = load_config()
        if not config:
            return False
        
        network = config.get('network', 'testnet')
        
        # Create mnemonic object (in production, this would come from secure storage)
        test_mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        mnemonic_obj = BIP39Mnemonic(mnemonic=test_mnemonic)
        
        # Create BIP44 derivation with correct signature
        derivation = BIP44Derivation(
            coin_type=0,  # Bitcoin/Evrmore coin type
            account=0,
            change="external-chain",
            address=0
        )
        
        # Use Evrmore class directly - network is determined by coin_type and address prefix
        hdwallet = HDWallet(cryptocurrency=Evrmore)
        hdwallet.from_mnemonic(mnemonic_obj)
        hdwallet.from_derivation(derivation)
        address = hdwallet.address()
        
        print(f"✅ HD Wallet derivation successful!")
        print(f"   Network: {network} (configured)")
        print(f"   Derived address: {address}")
        print(f"   Address prefix: {address[0]} ({'mainnet' if address[0] == 'E' else 'testnet'})")
        
        return True
        
    except ImportError as e:
        print(f"⚠️  HD Wallet library not available: {e}")
        print(f"   Install with: pip install hdwallet")
        return False
    except Exception as e:
        print(f"❌ HD Wallet test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("=" * 60)
    print("🧪 RPC Commands Test Suite")
    print("=" * 60)
    print()
    
    # Load configuration
    config = load_config()
    if not config:
        sys.exit(1)
    
    network = config.get('network', 'testnet')
    host = config.get('host', 'https://testnet-rpc.evrmorecoin.org')
    port = config.get('port', 443 if network == 'testnet' else 443)
    
    print(f"📋 Configuration:")
    print(f"   Network: {network}")
    print(f"   Host: {host}")
    print(f"   Port: {port}")
    print(f"   RPC Mode: {'Public Endpoint (No local node required)' if 'evrmorecoin.org' in host or host.startswith('https://') else 'Private RPC (Local node)'}")
    print(f"   Wallet: {config.get('wallet_address', 'Not configured')[:20]}...")
    print()
    
    # Run tests
    tests_passed = 0
    total_tests = 3
    
    print("-" * 60)
    print("Test 1: Basic RPC Connection (getblockchaininfo)")
    print("-" * 60)
    if test_rpc_connection(config, network):
        tests_passed += 1
    print()
    
    print("-" * 60)
    print("Test 2: UTXO Query (getaddressutxos)")
    print("-" * 60)
    if test_getaddressutxos(config):
        tests_passed += 1
    print()
    
    print("-" * 60)
    print("Test 3: HD Wallet Derivation")
    print("-" * 60)
    if test_hd_wallet():
        tests_passed += 1
    print()
    
    # Summary
    print("=" * 60)
    print(f"📊 Test Results: {tests_passed}/{total_tests} passed")
    print("=" * 60)
    
    if tests_passed == total_tests:
        print("✅ All tests passed! Bot is ready for deployment.")
        return 0
    else:
        print(f"⚠️  {total_tests - tests_passed} test(s) failed. Check configuration.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
