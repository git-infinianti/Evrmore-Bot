import os
import math
import asyncio
import logging
import sqlite3
import requests
from json import load
from datetime import datetime
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Modal, TextInput
from requests import post

class Call:
    def __init__(self, username, password, port) -> None:
        self.__call__: function = lambda method, parameters: post(
            f'http://localhost:{port}', 
            json = {
                'jsonrpc': '1.0',
                'id': 'python',
                'method': method,
                'params': list(parameters)
            }, auth = (username, password),
            headers = {'content-type': 'application/json'}
        ).json()['result']
    def __call__(self, method: str, *args) -> dict: # type: ignore
        return self.__call__(method, list(args))
    
    def __getattr__(self, method: str):
        def command(*args): return self.__call__(method, list(args))
        return command

load_dotenv()
TOKEN = os.environ['TOKEN']
PASSWORD = os.environ['PASSWORD']

with open('configuration.json') as file: data = load(file)
rpc = Call(data['user'], PASSWORD, data['port'])
ALLOWED_CHANNEL_IDS = data['allowed-channel-ids']
ALLOWED_CHANNEL_MENTIONS = ', '.join(f'<#{cid}>' for cid in ALLOWED_CHANNEL_IDS)

BOTNAME = data['prefix']
BOTADDRESS = data['default-address']
ADMINID = data['admin-id']
BOTUUIDS = data['bot-uuids']
EVRID = data['evr-id']
UNOFFID = data['unoff-id']
LOG_FILE = data['log']
PERMISSIONS = data['permissions-integer']
TXFEE = 1e-2
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


fh = logging.FileHandler(LOG_FILE)
fh.setLevel(logging.INFO)
ft = logging.Formatter('%(asctime)-15s - %(message)s')
fh.setFormatter(ft)

logger = logging.getLogger(BOTNAME)
logger.setLevel(logging.INFO)
logger.addHandler(fh)

class ChannelLockedTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id in ALLOWED_CHANNEL_IDS:
            return True
        msg = f"Wrong room for that one — bring your commands to {ALLOWED_CHANNEL_MENTIONS}."
        await interaction.response.send_message(embed=embed_message('🚪 WRONG CHANNEL', msg, RED), ephemeral=True)
        return False


intents = discord.Intents.all()
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents, tree_cls=ChannelLockedTree)

class NFTModal(Modal, title="Create NFT"):
    def __init__(self, user_id, rpc, logger, embed_message, red, green, tx_fee):
        super().__init__()
        self.user_id = str(user_id)
        self.rpc = rpc
        self.logger = logger
        self.embed_message = embed_message
        self.RED = red
        self.GREEN = green
        self.TXFEE = tx_fee

        self.asset_input = TextInput(label="Asset Name", placeholder="Unique NFT name")
        self.ipfs_input = TextInput(label="IPFS Hash", placeholder="Qm...")

        self.add_item(self.asset_input)
        self.add_item(self.ipfs_input)

    async def on_submit(self, interaction: discord.Interaction):
        asset = self.asset_input.value.upper()
        ipfs = self.ipfs_input.value
        account = self.user_id

        balance = self.rpc.getbalance(account)
        price = 5 + self.TXFEE

        if balance < price:
            msg = f"Minting costs {price} $EVR and your vault is a little light right now."
            await interaction.response.send_message(embed=self.embed_message('ERROR', msg, self.RED), ephemeral=True)
            return

        asset_tag = f"SHOP#{asset}"
        if self.rpc.listassets(asset_tag):
            msg = "That name's already claimed — get creative!"
            await interaction.response.send_message(embed=self.embed_message('ERROR', msg, self.RED), ephemeral=True)
            return

        address = self.rpc.getaccountaddress(account)
        wallet_address = self.rpc.getaccountaddress(HOUSE)
        self.rpc.move(account, HOUSE, price)
        tx = self.rpc.issue(asset_tag, 1, address, wallet_address, 1, False, True, ipfs)

        self.logger.info(f'{interaction.user} made: {asset_tag} TX: {tx}')
        msg = f'{interaction.user.mention} just minted something one-of-a-kind: `{asset_tag}`. Welcome to the vault.'
        await interaction.response.send_message(embed=self.embed_message('🎨 NEW NFT MINTED', msg, self.GREEN), ephemeral=True)
        

@bot.tree.command(name='menu', description='Open the interactive control panel')
async def menu_slash(interaction: discord.Interaction):
    user = interaction.user
    uuid = user.id
    class MenuView(View):
        @discord.ui.button(label='Balance Check', emoji='💰', style=discord.ButtonStyle.primary)
        async def balance(self, interaction: discord.Interaction, button: discord.ui.button):
            balance = rpc.getbalance(str(uuid))
            msg = f'{user.mention}, your vault is sitting at **{balance} $EVR**. Not bad.'
            await interaction.response.send_message(embed=embed_message('💰 BALANCE', msg, GREEN), ephemeral=True)
        @discord.ui.button(label='Asset Vault', emoji='🎒', style=discord.ButtonStyle.primary)
        async def asset_balance(self, interaction: discord.Interaction, button: discord.ui.button):
            #assets = rpc.listassets('*', False, 10, 0)
            assets = rpc.listassets()
            addresses = rpc.getaddressesbyaccount(str(uuid))
            asset_balances = get_asset_balances(addresses)
            if asset_balances is None:
                msg = 'Something glitched in the vault. Try again in a moment!'
                await interaction.response.send_message(embed=embed_message('⚠️ ERROR', msg, RED), ephemeral=True)
                return False
            elif len(asset_balances) > 0:
                embeds = []
                for asset in assets:
                    if asset in asset_balances.keys():
                        balance = asset_balances[asset]
                        msg = f'`{asset}` — **{balance}**'
                        embeds.append(embed_message('🎒 ASSET VAULT', msg, GREEN))
                await interaction.response.send_message(embeds=embeds, ephemeral=True)
            else:
                msg = f'{user.mention}, your vault is empty for now — time to change that.'
                await interaction.response.send_message(embed=embed_message('🎒 ASSET VAULT', msg, RED), ephemeral=True)
        
        @discord.ui.button(label='Create NFT', emoji='🎨', style=discord.ButtonStyle.success)
        async def nft(self, interaction: discord.Interaction, button: discord.ui.button):                
            await interaction.response.send_modal(
                NFTModal(str(uuid), rpc, logger, embed_message, RED, GREEN, TXFEE)
            )


        @discord.ui.button(label='Deposit', emoji='📥', style=discord.ButtonStyle.secondary)
        async def deposit(self, interaction: discord.Interaction, button: discord.ui.button):
            address = rpc.getaccountaddress(str(uuid))
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


def build_help_embed():
    title = "⚡ HERE'S EVERYTHING I CAN DO"
    msg = '''
    ***Get the rundown on how I work:*** 
    `/info`
    ***Check your $EVR vault:*** 
    `/balance`
    ***Check your $ASSET stash:*** 
    `/asset`
    ***Grab your deposit address:*** 
    `/deposit`
    ***Cash out your $EVR:*** 
    `/withdraw`
    ***Cash out an $ASSET:*** 
    `/redeem`
    ***Make it rain $EVR on everyone online:*** 
    `/rain`
    ***Shower an $ASSET on everyone online:*** 
    `/shower`
    ***Slide someone some $EVR:*** 
    `/tip`
    ***Slide someone an $ASSET:*** 
    `/send`
    ***Put in a buy order for an $ASSET:*** 
    `/buy`
    ***Check your open orders:*** 
    `/orders`
    ***Hunt down an $ASSET on the chain:*** 
    `/search`
    ***See where $EVR is trading:*** 
    `/price`
    ***See what time it is, universally:*** 
    `/time`
    '''
    return embed_message(title, msg, PURPLE)


def build_info_embed():
    msg = f"""
        Commands like /tip and /withdraw want their inputs in a specific order — here's the cheat sheet:
    
    ***Withdraw format:***                  
        `/withdraw [address] [amount]`
    
    ***Redeem format:***
        `/redeem [asset] [address] [amount]`

    ***Tip format:***
        `/tip [user] [amount]` 

    ***Send format:***
        `/send [user] [amount] [asset]`

    ***Rain format:***                 
        `/rain [amount]`

    ***Shower format:***
        `/shower [amount] [asset]`

    ***Buy format:***
        `/buy [asset] [amount] [price]`

    ***Search format:***
        `/search [asset]`
    

    *THE FINE PRINT*:
        - Not your keys, not your coins — you already know this.
        - Default transaction fee sits at {TXFEE} $EVR
        - Keep deposits reasonable (1 - 100,000 $EVR) — this isn't a long-term vault
        - Triple-check that $EVR address before you hit withdraw
        - I'm not responsible if things go sideways on your end
        ```USE ME AT YOUR OWN RISK```
    """
    return embed_message("📖 THE INFO DROP", msg, GREEN)


@bot.tree.command(name='invite', description='Get the bot invite link (admin only)')
async def invite_slash(interaction: discord.Interaction):
    user = interaction.user
    if user.id != ADMINID:
        msg = "This link is reserved for the inner circle. Nice try."
        await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
        return
    msg = f'Ready to spread the word? Drop this link and bring me to a new server:\n{INVITE_URL}'
    await interaction.response.send_message(embed=embed_message('🔗 INVITE LINK', msg, PURPLE), ephemeral=True)


@bot.tree.command(name='nft', description='Mint a new SHOP# NFT for 5 $EVR')
@app_commands.describe(asset='Unique NFT name', ipfs='IPFS hash for the NFT artwork')
async def nft_slash(interaction: discord.Interaction, asset: str, ipfs: str):
    user = interaction.user
    account = str(user.id)
    balance = rpc.getbalance(account)
    price = 5 + TXFEE
    if balance < price:
        msg = f'{user.mention}, minting costs {price} $EVR and your vault is a little light right now.'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    asset_tag = f'SHOP#{asset}'
    assets = rpc.listassets(asset_tag)
    if not assets:
        msg = "That name's already claimed — get creative!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    address = rpc.getaccountaddress(account)
    wallet_address = rpc.getaccountaddress(HOUSE)
    rpc.move(account, HOUSE, price)
    tx = rpc.issue(asset_tag, 1, address, wallet_address, 1, False, True, ipfs)
    logger.info(f'@{user.name}#{user.id} Made: {asset_tag} TX: {tx}')
    msg = f'{user.mention} just minted something one-of-a-kind: `{asset_tag}`. Welcome to the vault.'
    await interaction.response.send_message(embed=embed_message('🎨 NEW NFT MINTED', msg, GREEN))


@bot.tree.command(name='view', description='View details about an $ASSET')
@app_commands.describe(asset='Asset name to view')
async def view_slash(interaction: discord.Interaction, asset: str):
    user = interaction.user
    asset = asset.upper()
    if '/' in asset:
        split = asset.split('/')
        asset = f'{split[0].upper()}/{split[1].upper()}'
    if '#' in asset:
        split = asset.split('#')
        asset = f'{split[0].upper()}#{split[1]}'
    assets = rpc.listassets()
    if asset not in assets:
        msg = "That asset doesn't exist — double-check the name!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    asset_data = get_asset_info(asset)
    embed = discord.Embed(color=GREEN)
    msg = f'{user.mention} is putting `{asset}` under the microscope 🔍'
    embed.add_field(name="🔍 ASSET VIEW", value=msg, inline=False)
    if asset_data is not None:
        embed.add_field(name='Reissuable', value=str(reissuable(asset_data)), inline=True)
        embed.add_field(name='Divisible', value=str(divisible(asset)), inline=True)
    cid = get_cid(asset)
    if cid is not None:
        url = f'https://ipfs.io/ipfs/{cid}'
        embed.add_field(name='IPFS', value=url, inline=False)
        embed.set_footer(text=cid, icon_url=url)
        embed.set_image(url=url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name='sign', description='Sign a message with your deposit address')
@app_commands.describe(message='The message to sign')
async def sign_slash(interaction: discord.Interaction, message: str):
    user = interaction.user
    account = str(user.id)
    address = rpc.getaccountaddress(account)
    signature = rpc.signmessage(address, message)
    if signature is None:
        msg = 'Something snapped in the vault while signing that. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    logger.info(f'@{user.name}#{user.id} has Signed: {message} with Address: {address} and recieved Signature: {signature}')
    msg = f'{user.mention} signed `{message}` — here\'s your proof: `{signature}`'
    await interaction.response.send_message(embed=embed_message('✍️ SIGNED & SEALED', msg, GREEN), ephemeral=True)


@bot.tree.command(name='verify', description='Verify a signed message against your deposit address')
@app_commands.describe(signature='The signature to verify', message='The original message that was signed')
async def verify_slash(interaction: discord.Interaction, signature: str, message: str):
    user = interaction.user
    account = str(user.id)
    address = rpc.getaccountaddress(account)
    verified = rpc.verifymessage(address, signature, message)
    if not verified:
        msg = "That signature doesn't check out — something's off."
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    logger.info(f'@{user.name}#{user.id} has Verified: {message} with Signature: {signature}')
    msg = f'{user.mention}, that checks out — `{message}` is the real deal ✅'
    await interaction.response.send_message(embed=embed_message('✅ VERIFIED', msg, GREEN), ephemeral=True)


@bot.tree.command(name='transactions', description='View your transaction history')
async def transactions_slash(interaction: discord.Interaction):
    user = interaction.user
    account = str(user.id)
    transactions = rpc.listtransactions(account, 999999)
    if transactions is None:
        msg = 'Something glitched pulling your history. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if len(transactions) == 0:
        msg = "Nothing on the ledger yet — your history's a blank page."
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    for transaction in transactions:
        if transaction['category'] == 'move':
            continue
        account = transaction['account']
        address = transaction['address']
        amount = transaction['amount']
        blockhash = transaction['blockhash']
        blockindex = transaction['blockindex']
        blocktime = transaction['blocktime']
        blocktime = datetime.fromtimestamp(blocktime).strftime('%Y-%m-%d %H:%M:%S')
        category = transaction['category']
        confirmations = transaction['confirmations']
        txid = transaction['txid']
        time = transaction['time']
        time = datetime.fromtimestamp(time).strftime('%Y-%m-%d %H:%M:%S')
        time_received = transaction['timereceived']
        time_received = datetime.fromtimestamp(time_received).strftime('%Y-%m-%d %H:%M:%S')
        msg = f'Account: `{account}`\n' \
                f'Address: `{address}`\n' \
                f'Amount: `{amount}`\n' \
                f'Blockhash: `{blockhash}`\n' \
                f'Blockindex: `{blockindex}`\n' \
                f'Blocktime: `{blocktime}`\n' \
                f'Category: `{category}`\n' \
                f'Confirmations: `{confirmations}`\n' \
                f'Txid: `{txid}`\n' \
                f'Time: `{time}`\n' \
                f'Time Received: `{time_received}`\n'
        await interaction.followup.send(embed=embed_message('📜 TRANSACTION RECEIPT', msg, GREEN))


@bot.tree.command(name='wallet_balance', description='Check the total bot wallet balance (admin only)')
async def wallet_balance_slash(interaction: discord.Interaction):
    if interaction.user.id != ADMINID:
        msg = "This link is reserved for the inner circle. Nice try."
        await interaction.response.send_message(embed=embed_message('🔒 ACCESS DENIED', msg, RED), ephemeral=True)
        return
    balance = rpc.getbalance(HOUSE)
    msg = f'The house is holding **{balance} $EVR** right now.'
    await interaction.response.send_message(embed=embed_message('🏦 WALLET BALANCE', msg, GREEN), ephemeral=True)


@bot.tree.command(name='info', description='Learn the command formats and important notes')
async def info_slash(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_info_embed(), ephemeral=True)


@bot.tree.command(name='help', description='List every command at your disposal')
async def help_slash(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)


@bot.tree.command(name='balance', description='Check your $EVR vault balance')
async def balance_slash(interaction: discord.Interaction):
    user = interaction.user
    account = str(user.id)
    balance = rpc.getbalance(account)
    if balance is None:
        msg = 'Something glitched in the vault. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('⚠️ ERROR', msg, RED), ephemeral=True)
        return
    msg = f'{user.mention}, your vault is sitting at **{balance} $EVR**. Not bad.'
    await interaction.response.send_message(embed=embed_message('💰 BALANCE', msg, GREEN), ephemeral=True)


@bot.tree.command(name='asset', description='Check your $ASSET vault balances')
async def asset_slash(interaction: discord.Interaction):
    user = interaction.user
    account = str(user.id)
    assets = rpc.listassets()
    addresses = rpc.getaddressesbyaccount(account)
    asset_balances = get_asset_balances(addresses)
    if asset_balances is None:
        msg = 'Something glitched checking your stash. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if len(asset_balances) > 0:
        embeds = []
        for asset in assets:
            if asset in asset_balances.keys():
                balance = asset_balances[asset]
                msg = f'`{asset}` — **{balance}**'
                embeds.append(embed_message('🎒 ASSET VAULT', msg, GREEN))
        await interaction.response.send_message(embeds=embeds, ephemeral=True)
    else:
        msg = f'{user.mention}, your vault is empty for now — time to change that.'
        await interaction.response.send_message(embed=embed_message('🎒 ASSET VAULT', msg, RED), ephemeral=True)


@bot.tree.command(name='deposit', description='Get your deposit address for $EVR and assets')
async def deposit_slash(interaction: discord.Interaction):
    user = interaction.user
    account = str(user.id)
    address = rpc.getaccountaddress(account)
    if address is None:
        msg = 'Something glitched in the vault. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('⚠️ ERROR', msg, RED), ephemeral=True)
        return
    msg = f'{user.mention}, this is your door in. Send $EVR or assets straight here:'
    embed = discord.Embed(color=PURPLE)
    embed.add_field(name='📥 DEPOSIT ADDRESS', value=f'`{address}`', inline=False)
    embed.set_image(url=QR(address))
    embed.set_footer(text=address)
    await interaction.response.send_message(content=msg, embed=embed, ephemeral=True)


@bot.tree.command(name='withdraw', description='Withdraw your $EVR to an external address')
@app_commands.describe(address='Destination $EVR address', amount='Amount of $EVR to withdraw')
async def withdraw_slash(interaction: discord.Interaction, address: str, amount: float):
    user = interaction.user
    account = str(user.id)
    if not is_valid_address(address):
        msg = "That address doesn't look right — give it another look!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if not is_valid_amount(amount):
        msg = 'Give me a real amount to work with (ex: 1000)!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    balance = rpc.getbalance(account)
    if balance is None:
        msg = 'Something glitched checking your vault. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if balance < amount:
        msg = f"{user.mention}, your vault can't cover that withdrawal."
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    rpc.move(account, HOUSE, TXFEE)
    tx = rpc.sendfrom(account, address, amount-TXFEE)
    if tx is None:
        msg = 'Something snapped mid-transaction. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    logger.info(f'@{user.name}#{user.id} has Withdrawn: {amount} $EVR to Address: {address} with Transaction: {tx}')
    msg = f'{user.mention} just sent `{amount}` $EVR out into the wild — headed to `{address}`.'
    await interaction.response.send_message(embed=embed_message('💸 WITHDRAWAL SENT', msg, GREEN))


@bot.tree.command(name='redeem', description='Redeem an $ASSET to an external address')
@app_commands.describe(asset='Asset name', address='Destination $EVR address', amount='Amount to redeem')
async def redeem_slash(interaction: discord.Interaction, asset: str, address: str, amount: float):
    user = interaction.user
    account = str(user.id)
    asset = asset.upper()
    if not is_valid_address(address):
        msg = "That address doesn't look right — give it another look!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if not is_valid_amount(amount):
        msg = "Give me a real amount to work with (ex: 1000)!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    assets = rpc.listassets()
    if asset not in assets:
        msg = "That asset doesn't exist — try one that's actually on-chain (ex: EVR)!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    addresses = rpc.getaddressesbyaccount(account)
    balance = rpc.getbalance(account)
    has_asset, _, asset_balance = asset_in_addresses(asset, addresses)
    if not has_asset or asset_balance < amount or balance < TXFEE:
        msg = f"{user.mention}, you don't have enough ${asset} to pull that off."
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    rpc.move(account, HOUSE, TXFEE)
    own_address = rpc.getaccountaddress(account)
    tx = rpc.transferfromaddresses(asset, addresses, amount, address, '', 60, '', own_address)
    if tx is None:
        msg = 'Something snapped mid-transaction. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    logger.info(f'@{user.name}#{user.id} Redeemed: {amount} ${asset} to Address: {address}')
    msg = f'{user.mention} just redeemed {amount} ${asset} straight to `{address}`. Clean exit.'
    await interaction.response.send_message(embed=embed_message('📤 REDEMPTION COMPLETE', msg, GREEN))


@bot.tree.command(name='rain', description='Send some $EVR to all online members')
@app_commands.describe(amount='Total amount of $EVR to distribute')
async def rain_slash(interaction: discord.Interaction, amount: float):
    user = interaction.user
    account = str(user.id)
    if not is_valid_amount(amount) or amount > 1000000 or amount < 1:
        msg = "Keep it between 1 and 1,000,000 $EVR!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    guild = interaction.guild
    users = {f'{member.id}': member.id for member in guild.members if not member.bot and member.id !=
             user.id and member.raw_status in str(discord.Status.online)}
    user_count = len(users)
    if user_count == 0:
        msg = "Nobody's online to catch the drops right now!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    balance = rpc.getbalance(account)
    if balance < amount + TXFEE * user_count:
        msg = f"{user.mention}, your vault can't cover a storm that big."
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    await interaction.response.defer()
    pamount = amount/user_count
    members = []
    for key, value in users.items():
        await asyncio.sleep(0.1)
        member = discord.utils.get(guild.members, id=value)
        target_address = rpc.getaccountaddress(key)
        rpc.move(account, HOUSE, TXFEE)
        tx = rpc.sendfrom(account, target_address, pamount)
        if tx:
            logger.info(f'@{user.name}#{user.id} has Tipped: {pamount} $EVR to Account: {member.name}#{member.id} TX: {tx}')
            members.append(member.mention)
            continue
        msg = 'The storm fizzled out mid-drop. Try again in a moment!'
        await interaction.followup.send(embed=embed_message('ERROR', msg, RED))
        return
    members = ', '.join(members)
    msg = f'{user.mention} just made it rain — {pamount} $EVR each on {members}! ☔'
    await interaction.followup.send(embed=embed_message('☔ IT\'S RAINING $EVR', msg, GREEN))


@bot.tree.command(name='shower', description='Send some $ASSET to all online members')
@app_commands.describe(amount='Total amount to distribute', asset='Asset to distribute')
async def shower_slash(interaction: discord.Interaction, amount: float, asset: str):
    user = interaction.user
    account = str(user.id)
    asset = asset.upper()
    if not is_valid_amount(amount):
        msg = "Give me a real amount to work with (ex: 1000)!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    assets = rpc.listassets()
    if asset not in assets:
        msg = "That asset doesn't exist — try one that's actually on-chain (ex: EVR)!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    balance = rpc.getbalance(account)
    if balance is None:
        msg = 'Something glitched checking your vault. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    addresses = rpc.getaddressesbyaccount(account)
    has_asset, _, asset_balance = asset_in_addresses(asset, addresses)
    if not has_asset:
        msg = f"You don't have any ${asset} to share!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    guild = interaction.guild
    users = {f'{member.id}': member.id for member in guild.members if not member.bot and member.id !=
             user.id and member.raw_status in str(discord.Status.online)}
    user_count = len(users)
    if user_count == 0:
        msg = "Nobody's online to catch the drops right now!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    pamount = amount/user_count
    if pamount > asset_balance or balance < TXFEE * user_count:
        msg = "You don't have enough to cover a shower this size!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    await interaction.response.defer()
    members = []
    own_address = rpc.getaccountaddress(account)
    for key, value in users.items():
        await asyncio.sleep(0.1)
        member = discord.utils.get(guild.members, id=value)
        target_address = rpc.getaccountaddress(key)
        tx = rpc.transferfromaddresses(asset, addresses, pamount, target_address, '', 0, '', own_address)
        if tx is None:
            msg = 'The shower fizzled out mid-drop. Try again in a moment!'
            await interaction.followup.send(embed=embed_message('ERROR', msg, RED))
            return
        rpc.move(account, HOUSE, TXFEE)
        logger.info(f'@{user.name}#{user.id} has Showered: {pamount} ${asset} to Account: {member.name}#{member.id}')
        members.append(member.mention)
    members = ', '.join(members)
    msg = f'{user.mention} just showered {pamount} ${asset} each on {members}! 🚿'
    await interaction.followup.send(embed=embed_message('🚿 ASSET SHOWER', msg, GREEN))


@bot.tree.command(name='tip', description='Send a user some $EVR')
@app_commands.describe(user='The user to tip', amount='Amount of $EVR to send')
async def tip_slash(interaction: discord.Interaction, user: discord.Member, amount: float):
    sender = interaction.user
    account = str(sender.id)
    if not is_valid_amount(amount) or amount > 1000000 or amount < 1:
        msg = "Keep it between 1 and 1,000,000 $EVR!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if user.id == sender.id:
        msg = "Tipping yourself? That's not how this works."
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if user.id in BOTUUIDS:
        msg = "I appreciate the thought, but I don't take tips."
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    balance = rpc.getbalance(account)
    if balance is None:
        msg = 'Something glitched checking your vault. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if balance < amount:
        msg = f"{sender.mention}, your vault can't cover that tip."
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    target_account = str(user.id)
    target_address = rpc.getaccountaddress(target_account)
    rpc.move(account, HOUSE, TXFEE)
    tx = rpc.sendfrom(account, target_address, amount-TXFEE)
    if tx:
        logger.info(f'@{sender.name}#{sender.id} Tipped: @{user.name}#{user.id} {amount} $EVR TX: {tx}')
        msg = f'{sender.mention} just slid {user.mention} {amount} $EVR. Generosity looks good on you.'
        await interaction.response.send_message(embed=embed_message('💸 TIP SENT', msg, GREEN))
        return
    msg = 'Something snapped mid-transaction. Try again in a moment!'
    await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)


@bot.tree.command(name='send', description='Send a user some $ASSET')
@app_commands.describe(user='The user to send to', amount='Amount to send', asset='Asset to send')
async def send_slash(interaction: discord.Interaction, user: discord.Member, amount: float, asset: str):
    sender = interaction.user
    account = str(sender.id)
    asset = asset.upper()
    if not is_valid_amount(amount):
        msg = "Give me a real amount to work with (ex: 1000)!"
        await interaction.response.send_message(embed=embed_message('⚠️ SEND', msg, RED), ephemeral=True)
        return
    assets = rpc.listassets()
    if asset not in assets:
        msg = "That asset doesn't exist — try one that's actually on-chain (ex: EVR)!"
        await interaction.response.send_message(embed=embed_message('⚠️ SEND', msg, RED), ephemeral=True)
        return
    target_account = str(user.id)
    target_address = rpc.getaccountaddress(target_account)
    balance = rpc.getbalance(account)
    addresses = rpc.getaddressesbyaccount(account)
    has_asset, _, asset_balance = asset_in_addresses(asset, addresses)
    if not has_asset or asset_balance < amount or balance < TXFEE:
        msg = f"{sender.mention}, you don't have enough ${asset} to send that."
        await interaction.response.send_message(embed=embed_message('⚠️ SEND', msg, RED), ephemeral=True)
        return
    rpc.move(account, HOUSE, TXFEE)
    own_address = rpc.getaccountaddress(account)
    tx = rpc.transferfromaddresses(asset, addresses, amount, target_address, '', 0, '', own_address)
    if tx is None:
        msg = 'Something snapped mid-transaction. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('⚠️ SEND', msg, RED), ephemeral=True)
        return
    logger.info(f'@{sender.name}#{sender.id} Sent: {amount} ${asset} to @{user.name}#{user.id} with Transaction: {tx}')
    msg = f'{sender.mention} just sent {amount} ${asset} over to {user.mention}. Smooth handoff.'
    await interaction.response.send_message(embed=embed_message('📤 SEND COMPLETE', msg, GREEN))


@bot.tree.command(name='buy', description='Place a buy order for an $ASSET')
@app_commands.describe(asset='Asset to buy', amount='Amount to buy', price='Price per unit in $EVR')
async def buy_slash(interaction: discord.Interaction, asset: str, amount: float, price: float):
    user = interaction.user
    account = str(user.id)
    asset = asset.upper()
    if '#' in asset:
        split = asset.split('#')
        asset = f'{split[0].upper()}#{split[1]}'
    assets = rpc.listassets()
    if asset not in assets:
        msg = "That asset doesn't exist — try one that's actually on-chain (ex: EVR)!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if not is_valid_amount(amount):
        msg = "Give me a real amount to work with (ex: 1000)!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    if not is_valid_amount(price):
        msg = "Give me a real price to work with (ex: 0.1)!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    balance = rpc.getbalance(account)
    total = amount * price
    if balance < total + TXFEE:
        msg = "Your vault can't cover that order!"
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    rpc.move(account, HOUSE, total)
    nonce = int(datetime.utcnow().timestamp())
    connection = sqlite3.connect('buy.db')
    cursor = connection.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS buy (
        account TEXT, nonce INTEGER, type TEXT, asset TEXT, address TEXT,
        amount REAL, price REAL, total REAL, complete INTEGER, canceled INTEGER, txid TEXT
    )''')
    cursor.execute(
        '''INSERT INTO buy VALUES (:account, :nonce, :type, :asset, :address, :amount, :price, :total, :complete, :canceled, :txid)''',
        {
            'account': account,
            'nonce': nonce,
            'type': 'buy',
            'asset': asset,
            'address': '',
            'amount': amount,
            'price': price,
            'total': total,
            'complete': False,
            'canceled': False,
            'txid': None
        }
    )
    connection.commit()
    connection.close()
    logger.info(f'@{user.name}#{user.id} placed a buy order: {amount} {asset} @ {price} $EVR (total {total})')
    msg = f'{user.mention} just staked a claim: buying {amount} ${asset} at {price} $EVR each (total: {total} $EVR).'
    await interaction.response.send_message(embed=embed_message('📈 ORDER PLACED', msg, GREEN))


@bot.tree.command(name='orders', description='View your open buy orders')
async def orders_slash(interaction: discord.Interaction):
    user = interaction.user
    account = str(user.id)
    connection = sqlite3.connect('buy.db')
    cursor = connection.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS buy (
        account TEXT, nonce INTEGER, type TEXT, asset TEXT, address TEXT,
        amount REAL, price REAL, total REAL, complete INTEGER, canceled INTEGER, txid TEXT
    )''')
    cursor.execute(
        'SELECT nonce, asset, amount, price, total, complete, canceled FROM buy WHERE account = ? ORDER BY nonce DESC',
        (account,)
    )
    rows = cursor.fetchall()
    connection.close()
    if not rows:
        msg = "Your order book is empty — nothing in play right now."
        await interaction.response.send_message(embed=embed_message('📋 ORDERS', msg, RED), ephemeral=True)
        return
    lines = []
    for nonce, asset, amount, price, total, complete, canceled in rows:
        status = 'canceled' if canceled else ('complete' if complete else 'open')
        lines.append(f'`#{nonce}` {amount} ${asset} @ {price} $EVR (total: {total}) - {status}')
    msg = '\n'.join(lines)
    await interaction.response.send_message(embed=embed_message('📋 YOUR ORDERS', msg, GREEN), ephemeral=True)


@bot.tree.command(name='search', description='Search the blockchain for an $ASSET')
@app_commands.describe(name='Asset name (or prefix) to search for')
async def search_slash(interaction: discord.Interaction, name: str):
    name = name.upper()
    assets = rpc.listassets(f'{name}*')
    if len(assets) == 0:
        msg = f"Came up empty — nothing on-chain matches `{name}`."
        await interaction.response.send_message(embed=embed_message('🔎 SEARCH', msg, RED), ephemeral=True)
        return
    msg = '\n'.join(f'`{a}`' for a in assets)
    await interaction.response.send_message(embed=embed_message(f'🔎 {name}', msg, discord.Color.random()))


@bot.tree.command(name='price', description='Get the current market price of $EVR')
async def price_slash(interaction: discord.Interaction):
    try:
        response = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={'ids': 'evrmore', 'vs_currencies': 'usd'},
            timeout=10
        )
        response.raise_for_status()
        usd_price = response.json()['evrmore']['usd']
    except Exception:
        msg = 'The price feed went dark for a second there. Try again in a moment!'
        await interaction.response.send_message(embed=embed_message('ERROR', msg, RED), ephemeral=True)
        return
    msg = f'$EVR is trading at **${usd_price} USD** right now.'
    await interaction.response.send_message(embed=embed_message('📊 $EVR PRICE', msg, GREEN))


@bot.tree.command(name='time', description='Get the current UTC time')
async def time_slash(interaction: discord.Interaction):
    msg = f'Right now, everywhere: **{current_time()}**'
    await interaction.response.send_message(embed=embed_message('🕐 THE TIME', msg, GREEN))


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
    account = f'{uuid}'
    msg = message.content
    logger.info(f'{account}: {msg}')


def is_valid_address(address):
    validate_address = rpc.validateaddress(address)
    if validate_address['isvalid']: return True
    return False


def is_valid_amount(amount) -> bool:
    return isinstance(amount, float) and math.isfinite(amount) and amount > 0


def reissuable(asset_data: dict):
    if asset_data['reissuable'] == 1: return True
    return False


def divisible(asset_name: dict):
    asset_data = rpc.getassetdata(asset_name)
    if asset_data['divisible'] != 0: return asset_data['divisible']
    return False


def get_cid(asset_name):
    asset_data = rpc.getassetdata(asset_name)
    if asset_data['has_ipfs'] == 1: return asset_data['ipfs_hash']
    return None

def asset_in_addresses(asset, addresses):
    for address in addresses:
        balances = rpc.listassetbalancesbyaddress(address)
        if asset in balances: return True, address, balances[asset]
    return False, None, 0

def get_asset_balances(addresses: list):
    balances = {}
    for address in addresses:
        asset_balances = rpc.listassetbalancesbyaddress(address)
        for asset in asset_balances:
            if asset in balances:
                balances[asset] += asset_balances[asset]
            else:
                balances[asset] = asset_balances[asset]
    return balances

def get_asset_info(asset):
    assets = rpc.listassets(f'{asset}*', True)
    if asset in assets:
        return assets[asset]
    else:
        return None


def current_time():
    return datetime.utcnow().strftime('%a, %b/%d/%Y - %H:%M:%S UTC')


def embed_message(name, value, color):
    # description supports up to 4096 chars, vs 1024 for a field value — avoids Discord 400 errors on long messages
    return discord.Embed(title=name, description=value, color=color)


def main():
    bot.run(TOKEN)


if __name__ == '__main__':
    main()
