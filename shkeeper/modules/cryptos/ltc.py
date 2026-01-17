from shkeeper.modules.classes.bitcoin_like_crypto import BitcoinLikeCrypto
from os import environ


class ltc(BitcoinLikeCrypto):
    def __init__(self):
        self.crypto = "LTC"

    def getname(self):
        return "Litecoin"

    def gethost(self):
        rpc_url = environ.get("LTC_RPC_URL", "litecoind:9332")
        if rpc_url.startswith("https://"):
            return rpc_url.replace("https://", "").replace("http://", "")
        return rpc_url
