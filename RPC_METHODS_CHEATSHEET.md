# Evrmore RPC Whitelisted Methods Cheatsheet

> **Source**: [EvrmoreOrg/evrmore-rpc-proxy](https://github.com/EvrmoreOrg/evrmore-rpc-proxy/tree/master/lib)  
> **Filtered**: Essential methods only (52 commands)

---

## 📋 Quick Reference by Category

### == Addressindex ==

| Method | Description |
|--------|-------------|
| `getaddressbalance` | Get address balance |
| `getaddressdeltas` | Get address deltas |
| `getaddressmempool` | Get address mempool |
| `getaddresstxids` | Get address transaction IDs |
| `getaddressutxos` | Get address UTXOs |

---

### == Assets ==

| Method | Description |
|--------|-------------|
| `getassetdata` | Get asset data |
| `getburnaddresses` | Get burn addresses |
| `listaddressesbyasset` | List addresses by asset |
| `listassetbalancesbyaddress` | List asset balances by address |
| `listassets` | List assets |

---

### == Blockchain ==

| Method | Description |
|--------|-------------|
| `decodeblock` | Decode block |
| `getbestblockhash` | Get best block hash |
| `getblock` | Get block |
| `getblockchaininfo` | Get blockchain info |
| `getblockcount` | Get block count |
| `getblockhash` | Get block hash |
| `getblockheader` | Get block header |
| `getchaintips` | Get chain tips |
| `getchaintxstats` | Get chain tx stats |
| `getdifficulty` | Get difficulty |
| `getmempoolancestors` | Get mempool ancestors |
| `getmempooldescendants` | Get mempool descendants |
| `getmempoolentry` | Get mempool entry |
| `getmempoolinfo` | Get mempool info |
| `getrawmempool` | Get raw mempool |
| `getspentinfo` | Get spent info |
| `gettxout` | Get tx out |
| `gettxoutproof` | Get tx out proof |

---

### == Control ==

| Method | Description |
|--------|-------------|
| `help` | Get help |
| `getnetworkhashps` | Get network hash rate |

---

### == Rawtransactions ==

| Method | Description |
|--------|-------------|
| `combinerawtransaction` | Combine raw transactions |
| `createrawtransaction` | Create raw transaction |
| `decoderawtransaction` | Decode raw transaction |
| `decodescript` | Decode script |
| `getrawtransaction` | Get raw transaction |
| `sendrawtransaction` | Send raw transaction |
| `signrawtransaction` | Sign raw transaction |
| `testmempoolaccept` | Test mempool accept |

---

### == Restricted assets ==

| Method | Description |
|--------|-------------|
| `checkaddressrestriction` | Check address restriction |
| `checkaddresstag` | Check address tag |
| `checkglobalrestriction` | Check global restriction |
| `getverifierstring` | Get verifier string |
| `isvalidverifierstring` | Is valid verifier string |
| `listaddressesfortag` | List addresses for tag |
| `listaddressrestrictions` | List address restrictions |
| `listglobalrestrictions` | List global restrictions |
| `listtagsforaddress` | List tags for address |

---

### == Util ==

| Method | Description |
|--------|-------------|
| `estimatefee` | Estimate fee |
| `estimatesmartfee` | Estimate smart fee |
| `signmessagewithprivkey` | Sign message with privkey |
| `validateaddress` | Validate address |

---

### == Mining ==

| Method | Description |
|--------|-------------|
| `verifymessage` | Verify message |

---

## 🔌 Usage Examples

### Python (using PublicRpcClient pattern)

```python
from requests import post

class PublicRpcClient:
    def __init__(self, url, timeout=10):
        self.url = url.rstrip('/')
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
            
            response = post(self.url, json=payload, timeout=self.timeout)
            body = response.json()
            if body.get('error'):
                raise Exception(str(body['error']))
            return body.get('result')
        
        return _call

# Usage
rpc = PublicRpcClient('https://evr-rpc-mainnet.evrmorecoin.org/rpc')
info = rpc.getblockchaininfo()
print(info)
```

### cURL Example

```bash
curl -u username:password \
  -d '{"jsonrpc":"1.0","id":"1","method":"getblockchaininfo","params":[]}' \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8819/
```

---

## 📝 Notes

1. **Public RPC Endpoints**:
   - Mainnet: `https://evr-rpc-mainnet.evrmorecoin.org/rpc`
   - Testnet: `https://evr-rpc-testnet.evrmorecoin.org/rpc`

2. **Total Methods**: 52 whitelisted commands across 8 categories

3. **Method Availability**: Some methods may require wallet to be unlocked or specific node configuration

---

*Last updated: Based on evrmore-rpc-proxy master branch*
