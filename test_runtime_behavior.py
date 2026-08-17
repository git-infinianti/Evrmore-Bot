import os
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

os.environ.setdefault('TOKEN', 'test-token')
os.environ.setdefault('PASSWORD', 'test-password')

import discord_bot


class FeeRateTests(unittest.TestCase):
    def test_mainnet_uses_six_confirmation_smart_fee(self):
        mock_rpc = Mock()
        mock_rpc.estimatesmartfee.return_value = {'feerate': Decimal('0.0042')}
        mock_rpc.getmempoolinfo.return_value = {'mempoolminfee': Decimal('0.001')}
        mock_rpc.getnetworkinfo.return_value = {'relayfee': Decimal('0.001')}

        with (
            patch.object(discord_bot, 'NETWORK', 'mainnet'),
            patch.object(discord_bot, 'TXFEE_PER_KB_FLOOR', Decimal('0')),
            patch.object(discord_bot, 'rpc', mock_rpc),
        ):
            self.assertEqual(discord_bot.get_network_fee_rate(), Decimal('0.0042'))

        mock_rpc.estimatesmartfee.assert_called_once_with(6)

    def test_testnet_defaults_to_point_zero_one_zero_two_five(self):
        mock_rpc = Mock()
        mock_rpc.getmempoolinfo.return_value = {'mempoolminfee': Decimal('0.001')}
        mock_rpc.getnetworkinfo.return_value = {'relayfee': Decimal('0.001')}

        with (
            patch.object(discord_bot, 'NETWORK', 'testnet'),
            patch.object(discord_bot, 'TXFEE_PER_KB_FLOOR', Decimal('0.5')),
            patch.object(discord_bot, 'rpc', mock_rpc),
        ):
            self.assertEqual(discord_bot.get_network_fee_rate(), Decimal('0.01025'))

        mock_rpc.estimatesmartfee.assert_not_called()


class BalanceTests(unittest.TestCase):
    def test_balance_sums_only_spendable_evr_utxos(self):
        mock_rpc = Mock()
        mock_rpc.getaddressutxos.return_value = [
            {'satoshis': 125000000},
            {'assetName': 'EVR', 'satoshis': 25000000},
            {'assetName': 'TOKEN', 'satoshis': 99999999},
        ]

        with patch.object(discord_bot, 'rpc', mock_rpc):
            self.assertEqual(discord_bot.get_address_balance('address'), Decimal('1.5'))

    def test_balance_rpc_failure_is_not_reported_as_zero(self):
        mock_rpc = Mock()
        mock_rpc.getaddressutxos.side_effect = RuntimeError('rpc unavailable')

        with patch.object(discord_bot, 'rpc', mock_rpc):
            with self.assertRaisesRegex(RuntimeError, 'rpc unavailable'):
                discord_bot.get_address_balance('address')

    def test_asset_balance_falls_back_for_only_failed_addresses(self):
        mock_rpc = Mock()
        mock_rpc.listassetbalancesbyaddress.side_effect = [
            {'TOKEN': 2},
            RuntimeError('address index unavailable'),
        ]

        failed_address_utxos = [{
            'assetName': 'TOKEN',
            'amount': Decimal('3'),
        }]
        with (
            patch.object(discord_bot, 'rpc', mock_rpc),
            patch.object(discord_bot, 'get_address_utxos', return_value=failed_address_utxos) as get_utxos,
            patch.object(discord_bot, 'get_asset_units', return_value=0),
        ):
            balances = discord_bot.get_asset_balances(['working', 'failed'])

        self.assertEqual(balances, {'TOKEN': Decimal('5')})
        get_utxos.assert_called_once_with('failed', asset_name='*')


class TransactionStatusTests(unittest.TestCase):
    def summarize(self, transaction):
        mock_rpc = Mock()
        mock_rpc.getrawtransaction.return_value = transaction
        with patch.object(discord_bot, 'rpc', mock_rpc):
            return discord_bot.summarize_transaction('txid', set(), {}, {})

    def test_positive_confirmation_without_block_is_pending(self):
        summary = self.summarize({'vin': [], 'vout': [], 'confirmations': 1})
        self.assertEqual(summary['status'], 'PENDING')
        self.assertEqual(summary['confirmations'], 0)

    def test_positive_confirmation_with_block_is_confirmed(self):
        summary = self.summarize({
            'vin': [],
            'vout': [],
            'confirmations': 1,
            'blockhash': 'block-hash',
        })
        self.assertEqual(summary['status'], 'CONFIRMED')
        self.assertEqual(summary['confirmations'], 1)


if __name__ == '__main__':
    unittest.main()