"""
HD Wallet Provider for SHKeeper

Provides hierarchical deterministic (HD) wallet address generation using BIP32/39/44 standards.
Supports Bitcoin and Litecoin with thread-safe address derivation from encrypted seed phrase.

Architecture:
    - Encrypted seed phrase stored in hd_seed.enc (mounted read-only)
    - Addresses derived on-demand using BIP44 derivation paths
    - Thread-safe index allocation prevents address reuse
    - No private keys stored on disk (derived from seed as needed)

BIP44 Derivation Paths:
    Bitcoin:  m/44'/0'/0'/0/n (n = address index)
    Litecoin: m/44'/2'/0'/0/n (n = address index)

Security:
    - Seed encrypted with Fernet (AES-128-CBC + HMAC)
    - Encryption key from HD_WALLET_ENCRYPTION_KEY environment variable
    - Private keys never logged or stored permanently
    - Thread-safe to prevent race conditions
"""

import logging
import os
import threading
from decimal import Decimal
from pathlib import Path
from typing import Optional, Tuple

from bitcoinlib.keys import HDKey
from bitcoinlib.mnemonic import Mnemonic
from bitcoinlib.networks import Network
from cryptography.fernet import Fernet, InvalidToken

from shkeeper.utils import load_secret
logger = logging.getLogger(__name__)


# BIP44 Derivation Paths
# Format: m / purpose' / coin_type' / account' / change / address_index
# purpose: 44 (BIP44)
# coin_type: 0 (BTC), 2 (LTC), per SLIP-0044
# account: 0 (first account)
# change: 0 (external addresses for receiving)
BIP44_PATHS = {
    "BTC": "m/44'/0'/0'/0",
    "LTC": "m/44'/2'/0'/0",
}

# Bitcoin network identifiers
BITCOIN_NETWORKS = {
    "mainnet": "bitcoin",
    "testnet": "testnet",
}

# Litecoin network identifiers
LITECOIN_NETWORKS = {
    "mainnet": "litecoin",
    "testnet": "litecoin_testnet",
}


class HDWalletProvider:
    """
    Thread-safe HD wallet address derivation provider.

    This class manages HD wallet address generation from an encrypted BIP39 seed phrase.
    It maintains per-currency index counters to ensure unique addresses and uses
    threading.Lock() for thread safety during concurrent invoice creation.

    Attributes:
        currency: Cryptocurrency ticker (BTC, LTC)
        network: Network name (mainnet, testnet)
        master_key: Root HDKey derived from seed phrase
        _lock: Class-level thread lock for index allocation
        _index_counters: Class-level index tracking {currency: current_index}

    Example:
        >>> provider = HDWalletProvider(
        ...     currency="BTC",
        ...     seed_file="/app/hd_seed.enc",
        ...     encryption_key="<fernet-key>",
        ...     network="mainnet"
        ... )
        >>> address, index = provider.derive_next_address()
        >>> print(f"Address: {address}, Index: {index}")
        Address: bc1q..., Index: 0
    """

    # Class-level thread lock (shared across all instances)
    _lock = threading.Lock()

    # Class-level index counters (shared across all instances)
    # Format: {currency: current_index}
    _index_counters = {}

    def __init__(
        self,
        currency: str,
        seed_file: str,
        encryption_key: str,
        network: str = "mainnet"
    ):
        """
        Initialize HD wallet provider.

        Args:
            currency: Cryptocurrency ticker (BTC, LTC)
            seed_file: Path to encrypted seed file (e.g., /app/hd_seed.enc)
            encryption_key: Fernet encryption key (from HD_WALLET_ENCRYPTION_KEY)
            network: Network name (mainnet or testnet)

        Raises:
            ValueError: If currency not supported or network invalid
            FileNotFoundError: If seed file doesn't exist
            InvalidToken: If decryption fails (wrong key or corrupted file)
        """
        if currency not in BIP44_PATHS:
            raise ValueError(
                f"Unsupported currency: {currency}. "
                f"Supported: {list(BIP44_PATHS.keys())}"
            )

        if network not in ("mainnet", "testnet"):
            raise ValueError(f"Invalid network: {network}. Must be 'mainnet' or 'testnet'")

        self.currency = currency
        self.network = network

        # Load and decrypt seed phrase
        self._load_master_key(seed_file, encryption_key)

        # Initialize index counter for this currency (if not already initialized)
        with self._lock:
            if currency not in self._index_counters:
                self._index_counters[currency] = 0

        logger.info(
            f"HD wallet provider initialized for {currency} ({network})"
        )

    def _load_master_key(self, seed_file: str, encryption_key: str):
        """
        Load and decrypt seed phrase, then derive master key.

        Args:
            seed_file: Path to encrypted seed file
            encryption_key: Fernet encryption key

        Raises:
            FileNotFoundError: If seed file doesn't exist
            InvalidToken: If decryption fails
        """
        seed_path = Path(seed_file)
        if not seed_path.exists():
            raise FileNotFoundError(
                f"Encrypted seed file not found: {seed_file}\n"
                f"Run: python scripts/generate_hd_seed.py && python scripts/encrypt_hd_seed.py"
            )

        # Decrypt seed phrase
        try:
            cipher = Fernet(encryption_key.encode())
            encrypted_data = seed_path.read_bytes()
            seed_phrase = cipher.decrypt(encrypted_data).decode().strip()
        except InvalidToken:
            raise InvalidToken(
                "Failed to decrypt seed file. Check HD_WALLET_ENCRYPTION_KEY environment variable."
            )

        # Validate seed phrase (basic word count check)
        mnemo = Mnemonic()
        words = seed_phrase.split()
        if len(words) not in [12, 15, 18, 21, 24]:
            raise ValueError(
                f"Invalid BIP39 seed phrase. Expected 12/15/18/21/24 words, got: {len(words)} words"
            )

        # Convert seed phrase to binary seed (this will validate the mnemonic)
        try:
            seed_bytes = mnemo.to_seed(seed_phrase)
        except Exception as e:
            raise ValueError(f"Invalid BIP39 mnemonic phrase: {e}")

        # Derive master key from seed
        self.master_key = HDKey.from_seed(seed_bytes)

        logger.info(
            f"Master key loaded from encrypted seed ({len(seed_phrase.split())} words)"
        )

    def derive_next_address(self) -> Tuple[str, int]:
        """
        Derive next unique address for this currency with thread-safe index allocation.

        This method atomically allocates the next index and derives the corresponding
        address. Thread safety is critical for concurrent invoice creation.

        Returns:
            Tuple of (address, index)
                address: Derived cryptocurrency address
                index: BIP44 address index used

        Example:
            >>> address, index = provider.derive_next_address()
            >>> print(f"Address {index}: {address}")
            Address 0: bc1q...

        Thread Safety:
            Uses threading.Lock() to prevent race conditions during index allocation.
            Lock is held only during index increment (minimal critical section).
        """
        # Allocate next index atomically (critical section)
        with self._lock:
            index = self._index_counters[self.currency]
            self._index_counters[self.currency] += 1

        # Derive address outside lock for better performance
        address = self._derive_address_at_index(index)

        logger.debug(
            f"Derived {self.currency} address at index {index}: {address}"
        )

        return address, index

    def _derive_address_at_index(self, index: int) -> str:
        """
        Derive address at specific BIP44 index.

        Args:
            index: Address index in BIP44 derivation path

        Returns:
            Cryptocurrency address string

        Example:
            For BTC index 5:
                Path: m/44'/0'/0'/0/5
                Address: bc1q... (mainnet) or tb1q... (testnet)
        """
        # Get BIP44 path for currency
        base_path = BIP44_PATHS[self.currency]
        full_path = f"{base_path}/{index}"

        # Derive child key at path
        child_key = self.master_key.subkey_for_path(full_path)

        # Get network name for bitcoinlib
        if self.currency == "BTC":
            network_name = BITCOIN_NETWORKS[self.network]
        elif self.currency == "LTC":
            network_name = LITECOIN_NETWORKS[self.network]
        else:
            network_name = "bitcoin"  # Fallback

        # Generate address for network
        child_key.network = Network(network_name)
        address = child_key.address()

        return address

    def get_current_index(self) -> int:
        """
        Get current index counter for this currency.

        Returns:
            Current index (next address will use this index)

        Thread Safety:
            Uses lock to safely read counter value.
        """
        with self._lock:
            return self._index_counters.get(self.currency, 0)

    def derive_address_at_index(self, index: int) -> str:
        """
        Derive address at specific index without incrementing counter.

        Useful for address recovery or verification.

        Args:
            index: BIP44 address index

        Returns:
            Cryptocurrency address

        Example:
            >>> # Recover address at index 42
            >>> address = provider.derive_address_at_index(42)
        """
        return self._derive_address_at_index(index)


# Module-level helper function for easy integration
def create_hd_wallet_provider(
    currency: str,
    seed_file: Optional[str] = None,
    encryption_key: Optional[str] = None,
    network: Optional[str] = None
) -> HDWalletProvider:
    """
    Create HD wallet provider with defaults from environment.

    Args:
        currency: Cryptocurrency ticker (BTC, LTC)
        seed_file: Path to encrypted seed (default: from HD_WALLET_SEED_ENCRYPTED_FILE env)
        encryption_key: Fernet key (default: from HD_WALLET_ENCRYPTION_KEY env)
        network: Network name (default: from GETBLOCK_NETWORK or BTC_NETWORK env, fallback: mainnet)

    Returns:
        Configured HDWalletProvider instance

    Raises:
        ValueError: If required environment variables not set

    Example:
        >>> # Using environment variables
        >>> provider = create_hd_wallet_provider("BTC")
        >>>
        >>> # Override seed file
        >>> provider = create_hd_wallet_provider("LTC", seed_file="/custom/path/seed.enc")
    """
    if seed_file is None:
        seed_file = os.environ.get("HD_WALLET_SEED_ENCRYPTED_FILE")
        if not seed_file:
            raise ValueError(
                "HD_WALLET_SEED_ENCRYPTED_FILE environment variable not set"
            )

    if encryption_key is None:
        encryption_key = load_secret(
            "HD_WALLET_ENCRYPTION_KEY", "HD_WALLET_ENCRYPTION_KEY_FILE"
        )
        if not encryption_key:
            raise ValueError(
                "HD_WALLET_ENCRYPTION_KEY environment variable not set. "
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )

    if network is None:
        currency_key = currency.upper()
        if currency_key == "BTC":
            network = os.environ.get("BTC_NETWORK")
        elif currency_key == "LTC":
            network = os.environ.get("LTC_NETWORK")
        if not network:
            network = os.environ.get("GETBLOCK_NETWORK", "mainnet")

    return HDWalletProvider(
        currency=currency,
        seed_file=seed_file,
        encryption_key=encryption_key,
        network=network
    )
