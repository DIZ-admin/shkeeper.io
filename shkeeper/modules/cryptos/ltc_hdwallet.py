"""
Litecoin HD Wallet Crypto Module for SHKeeper

Extends the standard Litecoin (ltc) class to use HD wallet address generation
instead of RPC-based wallet methods. Uses GetBlock.io for blockchain monitoring.

This module auto-registers via SHKeeper's plugin architecture when:
    - LTC_ADDRESS_SOURCE=hdwallet in environment
    - HD wallet seed and encryption key are configured

Architecture:
    - Extends shkeeper.modules.cryptos.ltc.ltc
    - Overrides mkaddr() to use HDWalletProvider
    - Optionally overrides balance methods to use GetBlockClient
    - All other functionality inherited from parent

BIP44 Derivation Path:
    Litecoin: m/44'/2'/0'/0/n (coin_type=2 per SLIP-0044)

Configuration:
    Required environment variables:
        - LTC_ADDRESS_SOURCE=hdwallet
        - HD_WALLET_SEED_ENCRYPTED_FILE=/app/hd_seed.enc
        - HD_WALLET_ENCRYPTION_KEY=<fernet-key>
        - GETBLOCK_ACCESS_TOKEN=<getblock-token>

    Optional:
        - GETBLOCK_NETWORK=mainnet|testnet (default: mainnet)
"""

import logging
import os
from decimal import Decimal
from typing import Optional

from shkeeper.modules.cryptos.ltc import ltc
from shkeeper.modules.classes.hd_wallet import create_hd_wallet_provider
from shkeeper.modules.classes.getblock_client import create_getblock_client
from shkeeper.utils import load_secret

logger = logging.getLogger(__name__)


class LtcHDWallet(ltc):
    """
    Litecoin crypto module with HD wallet address generation.

    This class extends the standard ltc class but uses HD wallet for address
    generation instead of RPC wallet commands. It's activated when
    LTC_ADDRESS_SOURCE=hdwallet is set in the environment.

    Attributes:
        crypto: "LTC" ticker
        hd_provider: HDWalletProvider instance (lazy-loaded)
        getblock_client: GetBlockClient instance (lazy-loaded)
        use_getblock_for_balance: Whether to use GetBlock.io for balance queries

    Example:
        This class is automatically registered by SHKeeper when LTC_ADDRESS_SOURCE=hdwallet.
        No manual instantiation needed - SHKeeper's plugin system handles it.
    """

    def __init__(self):
        """Initialize Litecoin HD wallet crypto module."""
        super().__init__()
        self.crypto = "LTC"
        self.hd_provider: Optional[object] = None
        self.getblock_client: Optional[object] = None

        # Check if we should use GetBlock.io for balance queries
        # Default: False (use parent class methods)
        self.use_getblock_for_balance = os.environ.get(
            "LTC_USE_GETBLOCK_FOR_BALANCE",
            "false"
        ).lower() == "true"

        logger.info(
            "Litecoin HD Wallet module initialized "
            f"(GetBlock balance: {self.use_getblock_for_balance})"
        )

    def getname(self):
        """Get cryptocurrency name.

        Returns:
            str: "Litecoin (HD Wallet)"
        """
        return "Litecoin (HD Wallet)"

    def mkaddr(self, **kwargs):
        """
        Generate new Litecoin address using HD wallet.

        Overrides parent class mkaddr() to use HDWalletProvider instead of RPC.
        Addresses are derived deterministically from encrypted seed phrase using
        BIP44 path m/44'/2'/0'/0/n.

        Args:
            **kwargs: Optional keyword arguments (for compatibility with parent class)

        Returns:
            str: Litecoin address (L... or M... for mainnet, t... for testnet)

        Raises:
            ValueError: If HD wallet configuration missing
            FileNotFoundError: If encrypted seed file not found

        Example:
            >>> module = LtcHDWallet()
            >>> address = module.mkaddr()
            >>> print(address)
            LMo7yLFZb3D8V8YU9CcDR3Y8Q2R8...

        Notes:
            - Lazy-loads HDWalletProvider on first call
            - Thread-safe address derivation
            - Addresses are never reused (auto-increment index)
        """
        # Lazy-load HD wallet provider
        if self.hd_provider is None:
            try:
                self.hd_provider = create_hd_wallet_provider(currency="LTC")
                logger.info("HD wallet provider loaded for Litecoin")
            except Exception as e:
                logger.error(f"Failed to initialize HD wallet provider: {e}", exc_info=True)
                raise ValueError(
                    f"HD wallet configuration error: {e}\n"
                    "Ensure HD_WALLET_SEED_ENCRYPTED_FILE and HD_WALLET_ENCRYPTION_KEY are set."
                )

        # Derive next address
        try:
            address, index = self.hd_provider.derive_next_address()
            logger.info(f"Generated LTC address at index {index}: {address}")
            return address
        except Exception as e:
            logger.error(f"Failed to derive Litecoin address: {e}", exc_info=True)
            raise

    def getbalance(self, addr: str) -> Decimal:
        """
        Get balance for Litecoin address.

        Optionally uses GetBlock.io instead of parent class method if
        LTC_USE_GETBLOCK_FOR_BALANCE=true is set.

        Args:
            addr: Litecoin address to query

        Returns:
            Decimal: Balance in LTC

        Example:
            >>> module = LtcHDWallet()
            >>> balance = module.getbalance("LMo7...")
            >>> print(f"Balance: {balance} LTC")

        Notes:
            - If GetBlock.io is enabled, queries via JSON-RPC
            - Otherwise, falls back to parent class method (requires litecoind RPC)
        """
        if self.use_getblock_for_balance:
            # Use GetBlock.io for balance query
            if self.getblock_client is None:
                try:
                    self.getblock_client = create_getblock_client(currency="LTC")
                    logger.info("GetBlock.io client loaded for Litecoin")
                except Exception as e:
                    logger.error(f"Failed to initialize GetBlock client: {e}", exc_info=True)
                    # Fall back to parent class method
                    return super().getbalance(addr)

            try:
                balance = self.getblock_client.get_address_balance(addr)
                logger.debug(f"GetBlock.io balance for {addr}: {balance} LTC")
                return balance
            except Exception as e:
                logger.error(f"GetBlock.io balance query failed: {e}", exc_info=True)
                # Fall back to parent class method
                return super().getbalance(addr)
        else:
            # Use parent class method (litecoind RPC)
            return super().getbalance(addr)

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
class ltc_hdwallet(LtcHDWallet):
    """
    Lowercase alias for SHKeeper's auto-registration.

    SHKeeper's plugin system looks for classes named like the crypto ticker.
    This alias enables activation via LTC_ADDRESS_SOURCE=hdwallet.
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
            f"Litecoin HD Wallet module loaded but configuration incomplete. "
            f"Missing: {', '.join(missing)}"
        )
    else:
        logger.info("Litecoin HD Wallet module ready (configuration complete)")


# Run configuration check on module import
_check_configuration()
