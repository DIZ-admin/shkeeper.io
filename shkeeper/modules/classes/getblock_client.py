"""
GetBlock.io RPC Client for SHKeeper

Provides read-only blockchain access via GetBlock.io JSON-RPC API.
Supports Bitcoin and Litecoin for balance queries and transaction monitoring.

Architecture:
    - Synchronous HTTP client (httpx) for RPC calls
    - Read-only methods (listunspent, gettransaction, getblockcount)
    - Does NOT support wallet methods (getnewaddress, sendtoaddress)
    - Used by HD wallet crypto modules for payment monitoring

API Endpoint Format:
    https://go.getblock.io/{access_token}/

Supported Methods:
    - listunspent: Get unspent transaction outputs (UTXOs) for address
    - gettransaction: Get transaction details by txid
    - getblockcount: Get current blockchain height
    - getrawtransaction: Get raw transaction hex

Security:
    - Access token from GETBLOCK_ACCESS_TOKEN environment variable
    - HTTPS only
    - No wallet operations (read-only)
"""

import logging
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

from shkeeper.utils import load_secret
logger = logging.getLogger(__name__)


class GetBlockClient:
    """
    Synchronous GetBlock.io API client for blockchain queries.

    This client provides read-only access to Bitcoin and Litecoin blockchains
    via GetBlock.io JSON-RPC endpoints. It's used by HD wallet crypto modules
    to monitor payments without running local blockchain nodes.

    Attributes:
        access_token: GetBlock.io API access token
        currency: Cryptocurrency ticker (BTC, LTC)
        endpoint: Full RPC endpoint URL
        timeout: Request timeout in seconds

    Example:
        >>> client = GetBlockClient(
        ...     access_token="0b4b89b977204d4896a20cf3c8a0ddc6",
        ...     currency="BTC"
        ... )
        >>> balance = client.get_address_balance("bc1q...")
        >>> print(f"Balance: {balance} BTC")
    """

    # GetBlock.io endpoint template
    ENDPOINT_TEMPLATE = "https://go.getblock.io/{token}/"

    # Supported cryptocurrencies
    SUPPORTED_CURRENCIES = ["BTC", "LTC"]

    def __init__(
        self,
        access_token: str,
        currency: str,
        timeout: int = 30
    ):
        """
        Initialize GetBlock.io client.

        Args:
            access_token: GetBlock.io API access token
            currency: Cryptocurrency ticker (BTC, LTC)
            timeout: Request timeout in seconds (default: 30)

        Raises:
            ValueError: If currency not supported
        """
        if currency not in self.SUPPORTED_CURRENCIES:
            raise ValueError(
                f"Unsupported currency: {currency}. "
                f"Supported: {self.SUPPORTED_CURRENCIES}"
            )

        self.access_token = access_token
        self.currency = currency
        self.endpoint = self.ENDPOINT_TEMPLATE.format(token=access_token)
        self.timeout = timeout

        logger.info(
            f"GetBlock.io client initialized for {currency} "
            f"(endpoint: {self.endpoint[:40]}...)"
        )

    def _rpc_call(self, method: str, params: Optional[List] = None) -> Any:
        """
        Make JSON-RPC call to GetBlock.io.

        Args:
            method: RPC method name (e.g., "listunspent", "getblockcount")
            params: Method parameters (default: empty list)

        Returns:
            RPC result field value

        Raises:
            httpx.HTTPError: If HTTP request fails
            ValueError: If RPC returns error

        Example:
            >>> result = client._rpc_call("getblockcount")
            >>> print(f"Current block: {result}")
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": 1
        }

        try:
            response = httpx.post(
                self.endpoint,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()

            data = response.json()

            # Check for RPC error
            if "error" in data and data["error"] is not None:
                error = data["error"]
                error_message = error.get("message", str(error))
                raise ValueError(
                    f"RPC error calling {method}: {error_message}"
                )

            return data.get("result")

        except httpx.HTTPError as e:
            logger.error(
                f"HTTP error calling {method}: {e}",
                exc_info=True
            )
            raise

        except Exception as e:
            logger.error(
                f"Unexpected error calling {method}: {e}",
                exc_info=True
            )
            raise

    def get_address_balance(self, address: str) -> Decimal:
        """
        Get total balance for address using listunspent.

        This method queries all unspent transaction outputs (UTXOs) for the
        given address and sums their amounts to calculate the total balance.

        Args:
            address: Cryptocurrency address to query

        Returns:
            Total balance as Decimal

        Example:
            >>> balance = client.get_address_balance("bc1q...")
            >>> print(f"Balance: {balance} BTC")
            Balance: 0.00123456 BTC

        Notes:
            - Returns Decimal("0") if address has no UTXOs
            - Includes unconfirmed transactions (minconf=0)
            - Does NOT include spent outputs
        """
        try:
            # listunspent parameters:
            # minconf: 0 (include unconfirmed)
            # maxconf: 9999999 (all confirmations)
            # addresses: [address] (filter by address)
            utxos = self._rpc_call(
                "listunspent",
                [0, 9999999, [address]]
            )

            if not utxos:
                logger.debug(f"No UTXOs found for address {address}")
                return Decimal("0")

            # Sum all UTXO amounts
            total = sum(Decimal(str(utxo["amount"])) for utxo in utxos)

            logger.debug(
                f"Address {address} balance: {total} {self.currency} "
                f"({len(utxos)} UTXOs)"
            )

            return total

        except Exception as e:
            logger.error(
                f"Failed to get balance for {address}: {e}",
                exc_info=True
            )
            raise

    def get_address_transactions(self, address: str) -> List[Dict[str, Any]]:
        """
        Get all unspent transactions for address.

        Returns list of UTXO dictionaries containing transaction details.
        Useful for payment verification and monitoring.

        Args:
            address: Cryptocurrency address to query

        Returns:
            List of UTXO dicts with keys: txid, vout, address, amount, confirmations

        Example:
            >>> txs = client.get_address_transactions("bc1q...")
            >>> for tx in txs:
            ...     print(f"TX: {tx['txid']}, Amount: {tx['amount']}")

        Notes:
            - Only returns unspent outputs (not full transaction history)
            - Includes unconfirmed transactions
            - For full history, use blockchain explorer APIs
        """
        try:
            utxos = self._rpc_call(
                "listunspent",
                [0, 9999999, [address]]
            )

            logger.debug(
                f"Found {len(utxos)} UTXOs for address {address}"
            )

            return utxos

        except Exception as e:
            logger.error(
                f"Failed to get transactions for {address}: {e}",
                exc_info=True
            )
            raise

    def get_transaction(self, txid: str) -> Optional[Dict[str, Any]]:
        """
        Get transaction details by transaction ID.

        Args:
            txid: Transaction ID (hex string)

        Returns:
            Transaction dict or None if not found

        Example:
            >>> tx = client.get_transaction("abc123...")
            >>> print(f"Confirmations: {tx['confirmations']}")

        Notes:
            - Requires transaction to be in wallet or mempool
            - May not work for all transactions on GetBlock.io
            - For raw transaction hex, use get_raw_transaction()
        """
        try:
            tx = self._rpc_call("gettransaction", [txid])
            return tx
        except ValueError as e:
            # Transaction not found or not in wallet
            logger.warning(f"Transaction {txid} not found: {e}")
            return None
        except Exception as e:
            logger.error(
                f"Failed to get transaction {txid}: {e}",
                exc_info=True
            )
            raise

    def get_raw_transaction(self, txid: str, verbose: bool = True) -> Optional[Dict[str, Any]]:
        """
        Get raw transaction by ID with optional verbose output.

        Args:
            txid: Transaction ID
            verbose: If True, return decoded transaction; if False, return hex

        Returns:
            Transaction dict (if verbose=True) or hex string (if verbose=False)

        Example:
            >>> tx = client.get_raw_transaction("abc123...", verbose=True)
            >>> print(f"Inputs: {len(tx['vin'])}, Outputs: {len(tx['vout'])}")
        """
        try:
            tx = self._rpc_call(
                "getrawtransaction",
                [txid, 1 if verbose else 0]
            )
            return tx
        except ValueError as e:
            logger.warning(f"Raw transaction {txid} not found: {e}")
            return None
        except Exception as e:
            logger.error(
                f"Failed to get raw transaction {txid}: {e}",
                exc_info=True
            )
            raise

    def get_block_count(self) -> int:
        """
        Get current blockchain height.

        Returns:
            Current block number

        Example:
            >>> height = client.get_block_count()
            >>> print(f"Current block: {height}")
            Current block: 850000

        Notes:
            - Useful for checking node synchronization
            - Updates every ~10 minutes for Bitcoin
        """
        try:
            block_count = self._rpc_call("getblockcount")
            logger.debug(f"{self.currency} block count: {block_count}")
            return block_count
        except Exception as e:
            logger.error(
                f"Failed to get block count: {e}",
                exc_info=True
            )
            raise


# Module-level helper function for easy integration
def create_getblock_client(
    currency: str,
    access_token: Optional[str] = None
) -> GetBlockClient:
    """
    Create GetBlock.io client with defaults from environment.

    Args:
        currency: Cryptocurrency ticker (BTC, LTC)
        access_token: GetBlock.io API token (default: from GETBLOCK_ACCESS_TOKEN env)

    Returns:
        Configured GetBlockClient instance

    Raises:
        ValueError: If GETBLOCK_ACCESS_TOKEN not set

    Example:
        >>> # Using environment variable
        >>> client = create_getblock_client("BTC")
        >>>
        >>> # Override token
        >>> client = create_getblock_client("BTC", access_token="custom-token")
    """
    if access_token is None:
        currency_key = currency.upper()
        access_token = load_secret(
            f"GETBLOCK_ACCESS_TOKEN_{currency_key}",
            f"GETBLOCK_ACCESS_TOKEN_{currency_key}_FILE"
        )
        if not access_token:
            access_token = load_secret("GETBLOCK_ACCESS_TOKEN", "GETBLOCK_ACCESS_TOKEN_FILE")
        if not access_token:
            raise ValueError(
                "GETBLOCK_ACCESS_TOKEN environment variable not set. "
                "Get token from https://getblock.io/"
            )

    return GetBlockClient(
        access_token=access_token,
        currency=currency
    )
