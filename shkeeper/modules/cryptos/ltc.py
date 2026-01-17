from shkeeper.modules.classes.bitcoin_like_crypto import BitcoinLikeCrypto
from os import environ


class ltc(BitcoinLikeCrypto):
    def __init__(self):
        self.crypto = "LTC"

    def getname(self):
        return "Litecoin"

    def gethost(self):
        # Use environment variable or fallback to default
        rpc_url = environ.get('LTC_RPC_URL', 'litecoind:9332')
        # Remove https:// if present (for GetBlock.io compatibility)
        if rpc_url.startswith('https://'):
            # For HTTPS RPC, we need to return host without protocol
            # GetBlock.io URL: https://go.getblock.io/TOKEN/
            return rpc_url.replace('https://', '').replace('http://', '')
        return rpc_url
