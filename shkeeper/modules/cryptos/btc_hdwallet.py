"""
Bitcoin HD Wallet Crypto Module for SHKeeper

Extends the standard Bitcoin (Btc) class to use HD wallet address generation
instead of RPC-based wallet methods. Uses GetBlock.io for blockchain monitoring.

This module auto-registers via SHKeeper's plugin architecture when:
    - BTC_ADDRESS_SOURCE=hdwallet in environment
    - HD wallet seed and encryption key are configured

Architecture:
    - Extends shkeeper.modules.classes.btc.Btc
    - Overrides mkaddr() to use HDWalletProvider
    - Optionally overrides balance methods to use GetBlockClient
    - All other functionality (payouts, transactions) inherited from parent

Configuration:
    Required environment variables:
        - BTC_ADDRESS_SOURCE=hdwallet
        - HD_WALLET_SEED_ENCRYPTED_FILE=/app/hd_seed.enc
        - HD_WALLET_ENCRYPTION_KEY=<fernet-key>
        - GETBLOCK_ACCESS_TOKEN=<getblock-token>

    Optional:
        - GETBLOCK_NETWORK=mainnet|testnet (default: mainnet)
        - BTC_NETWORK=mainnet|testnet (default: mainnet)
"""

import logging
import os
from decimal import Decimal
from typing import Optional

from shkeeper.modules.classes.btc import Btc
from shkeeper.modules.classes.hd_wallet import create_hd_wallet_provider
from shkeeper.modules.classes.getblock_client import create_getblock_client
from shkeeper.utils import load_secret

logger = logging.getLogger(__name__)


class BtcHDWallet(Btc):
    """
    Bitcoin crypto module with HD wallet address generation.

    This class extends the standard Btc class but uses HD wallet for address
    generation instead of RPC wallet commands. It's activated when
    BTC_ADDRESS_SOURCE=hdwallet is set in the environment.

    Attributes:
        crypto: "BTC" ticker
        hd_provider: HDWalletProvider instance (lazy-loaded)
        getblock_client: GetBlockClient instance (lazy-loaded)
        use_getblock_for_balance: Whether to use GetBlock.io for balance queries

    Example:
        This class is automatically registered by SHKeeper when BTC_ADDRESS_SOURCE=hdwallet.
        No manual instantiation needed - SHKeeper's plugin system handles it.
    """

    def __init__(self):
        """Initialize Bitcoin HD wallet crypto module."""
        super().__init__()
        self.crypto = "BTC"
        self.hd_provider: Optional[object] = None
        self.getblock_client: Optional[object] = None

        # Check if we should use GetBlock.io for balance queries
        # Default: False (use parent class methods)
        self.use_getblock_for_balance = os.environ.get(
            "BTC_USE_GETBLOCK_FOR_BALANCE",
            "false"
        ).lower() == "true"

        logger.info(
            "Bitcoin HD Wallet module initialized "
            f"(GetBlock balance: {self.use_getblock_for_balance})"
        )

    def getname(self):
        """Get cryptocurrency name.

        Returns:
            str: "Bitcoin (HD Wallet)"
        """
        return "Bitcoin (HD Wallet)"

    def mkaddr(self, **kwargs):
        """
        Generate new Bitcoin address using HD wallet.

        Overrides parent class mkaddr() to use HDWalletProvider instead of RPC.
        Addresses are derived deterministically from encrypted seed phrase.

        Args:
            **kwargs: Optional keyword arguments (for compatibility with parent class)

        Returns:
            str: Bitcoin address (bc1... for mainnet, tb1... for testnet)

        Raises:
            ValueError: If HD wallet configuration missing
            FileNotFoundError: If encrypted seed file not found

        Example:
            >>> module = BtcHDWallet()
            >>> address = module.mkaddr()
            >>> print(address)
            bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh

        Notes:
            - Lazy-loads HDWalletProvider on first call
            - Thread-safe address derivation
            - Addresses are never reused (auto-increment index)
        """
        # Lazy-load HD wallet provider
        if self.hd_provider is None:
            try:
                self.hd_provider = create_hd_wallet_provider(currency="BTC")
                logger.info("HD wallet provider loaded for Bitcoin")
            except Exception as e:
                logger.error(f"Failed to initialize HD wallet provider: {e}", exc_info=True)
                raise ValueError(
                    f"HD wallet configuration error: {e}\n"
                    "Ensure HD_WALLET_SEED_ENCRYPTED_FILE and HD_WALLET_ENCRYPTION_KEY are set."
                )

        # Derive next address
        try:
            address, index = self.hd_provider.derive_next_address()
            logger.info(f"Generated BTC address at index {index}: {address}")
            return address
        except Exception as e:
            logger.error(f"Failed to derive Bitcoin address: {e}", exc_info=True)
            raise

    def getbalance(self, addr: str) -> Decimal:
        """
        Get balance for Bitcoin address.

        Optionally uses GetBlock.io instead of parent class method if
        BTC_USE_GETBLOCK_FOR_BALANCE=true is set.

        Args:
            addr: Bitcoin address to query

        Returns:
            Decimal: Balance in BTC

        Example:
            >>> module = BtcHDWallet()
            >>> balance = module.getbalance("bc1q...")
            >>> print(f"Balance: {balance} BTC")

        Notes:
            - If GetBlock.io is enabled, queries via JSON-RPC
            - Otherwise, falls back to parent class method (requires bitcoin-shkeeper)
        """
        if self.use_getblock_for_balance:
            # Use GetBlock.io for balance query
            if self.getblock_client is None:
                try:
                    self.getblock_client = create_getblock_client(currency="BTC")
                    logger.info("GetBlock.io client loaded for Bitcoin")
                except Exception as e:
                    logger.error(f"Failed to initialize GetBlock client: {e}", exc_info=True)
                    # Fall back to parent class method
                    return super().getbalance(addr)

            try:
                balance = self.getblock_client.get_address_balance(addr)
                logger.debug(f"GetBlock.io balance for {addr}: {balance} BTC")
                return balance
            except Exception as e:
                logger.error(f"GetBlock.io balance query failed: {e}", exc_info=True)
                # Fall back to parent class method
                return super().getbalance(addr)
        else:
            # Use parent class method (bitcoin-shkeeper API)
            return super().getbalance(addr)

    def getaddrbytx(self, txid: str):
        """
        Get address and amount from transaction using GetBlock.io.

        This method is called by SHKeeper's walletnotify handler to process
        incoming transactions.

        Args:
            txid: Transaction ID (hash)

        Returns:
            List of [address, amount, confirmations, category] for each output
            Example: [["bc1q...", Decimal("0.001"), 3, "receive"], ...]

        Note:
            Overrides parent class method to use GetBlock.io instead of
            bitcoin-shkeeper sidecar service.
        """
        try:
            # Initialize GetBlock client if needed
            if self.getblock_client is None:
                try:
                    self.getblock_client = create_getblock_client(currency="BTC")
                    logger.info("GetBlock.io client initialized for transaction lookup")
                except Exception as e:
                    logger.error(f"Failed to initialize GetBlock client: {e}", exc_info=True)
                    raise ValueError(f"GetBlock.io not available: {e}")

            # Get raw transaction with verbose=True for decoded data
            logger.debug(f"Fetching transaction {txid} from GetBlock.io")
            tx_data = self.getblock_client._rpc_call("getrawtransaction", [txid, True])

            result = []
            confirmations = tx_data.get("confirmations", 0)

            # Process all outputs (vout) in the transaction
            for vout in tx_data.get("vout", []):
                script_pub_key = vout.get("scriptPubKey", {})

                # Extract addresses from scriptPubKey
                addresses = script_pub_key.get("addresses", [])
                if not addresses and "address" in script_pub_key:
                    # Newer Bitcoin Core versions use "address" field
                    addresses = [script_pub_key["address"]]

                amount = Decimal(str(vout.get("value", 0)))

                # Add each address from this output to results
                for address in addresses:
                    # Category: "receive" for HD wallet-generated addresses
                    result.append([address, amount, confirmations, "receive"])

            logger.info(
                f"Transaction {txid} decoded: {len(result)} outputs, "
                f"{confirmations} confirmations"
            )

            from flask import current_app as app
            app.logger.warning(f"Transaction {txid} response: {result}")

            return result

        except Exception as e:
            logger.error(
                f"Failed to fetch transaction {txid} from GetBlock.io: {e}",
                exc_info=True
            )
            raise ValueError(f"GetBlock.io transaction fetch failed: {e}")

    def create_wallet(self, *args, **kwargs):
        """
        Create wallet (no-op for HD wallet).

        HD wallet uses deterministic seed phrase, so no wallet creation needed.
        This method is kept for compatibility with SHKeeper's wallet initialization.

        Returns:
            dict: {"error": None} indicating success
        """
        logger.info("HD wallet does not require create_wallet() - using seed phrase")
        return {"error": None}


# Compatibility alias for SHKeeper's plugin system
# SHKeeper expects lowercase class name matching crypto ticker
class btc_hdwallet(BtcHDWallet):
    """
    Lowercase alias for SHKeeper's auto-registration.

    SHKeeper's plugin system looks for classes named like the crypto ticker.
    This alias enables activation via BTC_ADDRESS_SOURCE=hdwallet.
    """
    pass


# Registration check
def _check_configuration():
    """
    Check if HD wallet configuration is complete.

    Logs warnings if required environment variables are missing.
    This function is called during module import for early detection.
    """
    required_values = {
        "HD_WALLET_SEED_ENCRYPTED_FILE": os.environ.get("HD_WALLET_SEED_ENCRYPTED_FILE"),
        "HD_WALLET_ENCRYPTION_KEY": load_secret(
            "HD_WALLET_ENCRYPTION_KEY", "HD_WALLET_ENCRYPTION_KEY_FILE"
        ),
        "GETBLOCK_ACCESS_TOKEN": load_secret(
            "GETBLOCK_ACCESS_TOKEN", "GETBLOCK_ACCESS_TOKEN_FILE"
        ),
    }

    missing = [var for var, value in required_values.items() if not value]

    if missing:
        logger.warning(
            f"Bitcoin HD Wallet module loaded but configuration incomplete. "
            f"Missing: {', '.join(missing)}"
        )
    else:
        logger.info("Bitcoin HD Wallet module ready (configuration complete)")


# Run configuration check on module import
_check_configuration()
